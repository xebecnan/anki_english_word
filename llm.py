# coding: utf-8
"""LLM 访问模块：配置管理 + OpenAI 兼容 Chat Completions 客户端

为什么独立成模块：
- main.py 只关心「发 prompt → 拿回答文本」，不关心是哪家 provider
- 换 LLM 服务商只需改 config.yaml（base_url / model / api_key），
  因为主流服务商（DeepSeek、OpenAI、Kimi、通义、各类中转站）都兼容
  OpenAI Chat Completions 协议，一套 requests 代码即可通吃

配置结构（config.yaml）：
    llm:
      active: deepseek            # 当前使用的 provider 名称
      providers:
        deepseek:
          base_url: https://api.deepseek.com
          model: deepseek-chat
          api_key: sk-xxx
        openai:
          base_url: https://api.openai.com/v1
          model: gpt-4o-mini
          api_key: ${OPENAI_API_KEY}   # 支持 ${VAR} 形式的环境变量引用
    proxies:                        # 代理，供 Google TTS 等境外服务使用
      http: http://localhost:8123
      https: http://localhost:8123

api_key 的解析优先级（从高到低）：
1. {PROVIDER大写}_API_KEY 环境变量（如 DEEPSEEK_API_KEY，兼容旧版行为）
2. 配置文件中的 api_key；其中 ${VAR} 引用已在 load_config 阶段展开，
   运行时与字面值等价（变量未定义则解析为空并警告）
"""

import json
import os
import re
import sys
import time

import requests
import yaml

CONFIG_FILE = 'config.yaml'
LEGACY_CONFIG_FILE = 'config.json'

# 占位 key：首次生成的模板里的假 key，用于检测「用户还没填 key」
SAMPLE_API_KEY = 'sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'

# 环境变量引用语法：${VAR_NAME}
_ENV_VAR_PATTERN = re.compile(r'\$\{(\w+)\}')

# 首次生成的最小配置模板（带注释，引导用户填 key）
_DEFAULT_CONFIG_TEMPLATE = '''\
# Anki 英语单词卡片生成器配置
# 使用方法：给 active 指向的 provider 填上真实的 api_key 即可
# 更多 provider 配置示例（OpenAI、中转站等）见 config.yaml.example

llm:
  active: deepseek
  providers:
    deepseek:
      base_url: https://api.deepseek.com
      model: deepseek-chat
      api_key: {sample_key}

# 网络代理：用于 Google TTS 等需要翻墙的服务，不影响 LLM API 请求
proxies:
  http: http://localhost:8123
  https: http://localhost:8123
'''


class ConfigError(Exception):
    """配置缺失或非法（缺 key、缺 provider 等）"""


class LLMError(Exception):
    """LLM API 调用失败（网络错误、HTTP 错误、返回格式错误等）"""


def _expand_env_vars(value):
    """递归展开配置中的 ${VAR} 环境变量引用

    为什么只在字符串值上做正则替换而不是整个文件预处理：
    yaml 值里可能合法地出现 '$'，逐值替换不会误伤其他内容

    为什么变量未定义时保留 ${VAR} 原样而不是警告并展开为空：
    这里处理的是整份配置，包括未启用的 provider（如示例里的 openai），
    在这里警告会对没用到的配置产生噪音；残留引用留给 _resolve_api_key，
    只在 active provider 真正使用时才警告
    """
    if isinstance(value, str):
        def replace(match):
            env = os.environ.get(match.group(1))
            # 未定义时保留 ${VAR} 原样，交由使用方（_resolve_api_key）处理
            return env if env is not None else match.group(0)
        return _ENV_VAR_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value


def _resolve_api_key(provider_name, provider_conf):
    """按优先级解析 api_key（见模块 docstring 的优先级说明）"""
    # {PROVIDER}_API_KEY 环境变量优先于配置文件字面值
    # 为什么环境变量优先：密钥不入库更安全，且兼容旧版 DEEPSEEK_API_KEY 的行为
    env_key = os.environ.get(f'{provider_name.upper()}_API_KEY')
    if env_key:
        return env_key

    # ${VAR} 显式引用：已定义的变量在 load_config 阶段已展开，
    # 走到这里说明存在未定义变量的残留引用。这是 active provider
    # 真正用 key 的时刻，此时才警告，避免未启用 provider 触发噪音
    def _warn_missing(match):
        print(f'警告: 环境变量 {match.group(1)} 未定义，api_key 解析为空')
        return ''
    return _ENV_VAR_PATTERN.sub(_warn_missing, provider_conf.get('api_key', ''))


def _create_default_config():
    """首次运行：生成带注释的 config.yaml 模板并提示用户填写

    返回生成的配置 dict。模板里是占位 key：
    - 环境变量里已有对应 key 时可直接运行（与旧版行为一致）
    - 否则调用方的校验会拦住并退出
    """
    content = _DEFAULT_CONFIG_TEMPLATE.format(sample_key=SAMPLE_API_KEY)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.write(content)
    # 提示语区分两种情况：环境变量能救活占位配置时不要误导用户去改文件
    env_name = 'DEEPSEEK_API_KEY'
    if os.environ.get(env_name):
        print(f'已生成默认配置文件 {CONFIG_FILE}（检测到环境变量 {env_name}，可直接运行）')
    else:
        print(f'已生成默认配置文件 {CONFIG_FILE}，请填写 api_key 后重新运行')
    return _expand_env_vars(yaml.safe_load(content))


def _migrate_legacy_config():
    """旧版 config.json → config.yaml 自动迁移

    为什么要自动迁移：旧版用户的真实 key 在 config.json 里，
    升级后如果直接报「找不到 config.yaml」会把人卡住。
    迁移后保留原 config.json 不删，避免误删用户数据。
    """
    if not os.path.exists(LEGACY_CONFIG_FILE):
        return None

    print(f'检测到旧版配置文件 {LEGACY_CONFIG_FILE}，自动迁移为 {CONFIG_FILE} ...')
    with open(LEGACY_CONFIG_FILE, 'r', encoding='utf-8') as f:
        legacy = json.load(f)

    api_key = legacy.get('API_KEY', '')
    proxies = legacy.get('PROXIES') or {
        'http': 'http://localhost:8123',
        'https': 'http://localhost:8123',
    }

    # 旧配置只有一组 DeepSeek 参数，对应迁移为 deepseek provider
    config = {
        'llm': {
            'active': 'deepseek',
            'providers': {
                'deepseek': {
                    'base_url': 'https://api.deepseek.com',
                    'model': 'deepseek-chat',
                    'api_key': api_key,
                }
            },
        },
        'proxies': proxies,
    }

    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f'迁移完成，原 {LEGACY_CONFIG_FILE} 已保留，可自行删除')
    if api_key == SAMPLE_API_KEY:
        print('注意: 旧配置中是占位 api_key，请在 config.yaml 中填写真实 key')
    return config


def load_config(config_file=CONFIG_FILE):
    """加载配置文件；不存在时自动迁移旧配置或生成默认模板

    返回: 展开环境变量后的完整配置 dict
    抛出: ConfigError 当配置文件存在但结构非法时
    """
    if not os.path.exists(config_file):
        migrated = _migrate_legacy_config()
        if migrated is not None:
            return migrated
        return _create_default_config()

    with open(config_file, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ConfigError(f'{config_file} 内容为空或格式非法')
    return _expand_env_vars(data)


def validate_llm_config(config):
    """校验 llm 配置节并返回 (active 名称, provider 配置)

    单独拆出校验是为了让 create_client 的职责保持单一：
    一个管「配置对不对」，一个管「客户端怎么建」
    """
    llm_conf = config.get('llm') or {}
    providers = llm_conf.get('providers')
    if not providers:
        raise ConfigError('config.yaml 缺少 llm.providers 配置')

    active = llm_conf.get('active')
    if not active:
        raise ConfigError('config.yaml 缺少 llm.active 字段（指定使用哪个 provider）')
    if active not in providers:
        raise ConfigError(f"llm.active 为 {active!r}，但 providers 中只有: {', '.join(providers)}")

    return active, providers[active]


class LLMClient:
    """OpenAI 兼容 Chat Completions 客户端（requests 手写实现）

    为什么不引入 openai SDK：项目只用最基础的 chat completions 能力，
    requests 手写几十行即可，避免为一个简单场景增加重依赖。
    """

    def __init__(self, base_url, api_key, model,
                 timeout=25, temperature=1.0, top_p=1.0, retries=5):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.top_p = top_p
        self.retries = retries

    def _build_url(self):
        """拼接完整请求 URL

        为什么做后缀判断：用户容易把完整 endpoint（.../chat/completions）
        和根路径（https://api.deepseek.com）混填，这里宽容处理两种写法
        """
        if self.base_url.endswith('/chat/completions'):
            return self.base_url
        return self.base_url + '/chat/completions'

    def chat(self, prompt, system_prompt=''):
        """发送一条 user 消息并返回回答文本，内置指数退避重试

        为什么重试放在这里而不是调用方：main.py 原来在 5 个调用点
        各写了一遍相同的 retry 循环，下沉后调用方只需一行
        """
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.api_key}',
        }
        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': prompt})

        payload = {
            'model': self.model,
            'messages': messages,
            'temperature': self.temperature,
            'top_p': self.top_p,
            'n': 1,
            # stream 在 _request_once 里统一设为 True，这里不重复指定
            'presence_penalty': 0,
            'frequency_penalty': 0,
        }

        last_error = None
        for retry in range(self.retries):
            if retry > 0:
                # 指数退避：1s, 2s, 4s, 8s ...，给限流/网络抖动留恢复时间
                sleep_seconds = 2 ** retry
                print(f'LLM 请求失败，{sleep_seconds}s 后重试 ({retry + 1}/{self.retries})')
                time.sleep(sleep_seconds)
            try:
                return self._request_once(headers, payload)
            except LLMError as e:
                last_error = e
                print('<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')
                print(e)
                print('<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')

        raise LLMError(f'LLM 请求重试 {self.retries} 次后仍失败: {last_error}')

    def _request_once(self, headers, payload):
        """单次请求（SSE 流式），任何失败都抛 LLMError（由 chat 统一重试）

        为什么用流式而不是等完整回答（stream=False）：
        切换到 GLM 这类推理模型后，服务端会先输出大量「思考」token 再给
        正式回答，非流式请求必须一口气等完「思考 + 回答」；叠加 coding
        端点的排队波动（实测同一请求一次超 120s、一次 47s），无论
        timeout 设多大都赌运气。流式下分块持续到达，每次 socket read
        都会重置超时计时器，「服务端生成多久」不再拖垮客户端；
        timeout 只需要覆盖「首块等待 + 单次读间隔」
        """
        payload = {**payload, 'stream': True}
        try:
            response = requests.post(
                url=self._build_url(),
                headers=headers,
                json=payload,
                stream=True,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise LLMError(f'网络请求失败: {e}') from e

        # HTTP 层错误（key 无效 401、路径错误 404 等）：错误体不是 SSE，
        # 实测这类错误服务端秒回，直接读 body 报告即可
        if response.status_code != 200:
            body = response.text[:200]
            response.close()
            raise LLMError(f'HTTP {response.status_code}: {body}')

        content_parts = []
        first_line = ''   # 留样：解析不出内容时帮助定位响应到底是什么
        try:
            for line in response.iter_lines():
                if not line:
                    continue
                text = line.decode('utf-8')
                if not first_line:
                    first_line = text[:200]
                if not text.startswith('data:'):
                    continue
                data = text[len('data:'):].strip()
                if data == '[DONE]':
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    # 个别坏块丢弃即可，不拖垮整个请求
                    continue
                # 部分服务端在 HTTP 200 的流内报业务错误（key 无效、余额不足等）
                if 'error' in chunk:
                    err = chunk['error']
                    message = err.get('message', err) if isinstance(err, dict) else err
                    raise LLMError(f'LLM API 错误: {message}')
                choices = chunk.get('choices') or []
                if not choices:
                    continue
                delta = choices[0].get('delta') or {}
                # 推理模型的思考内容在 reasoning_content 里，正式回答才是
                # 非 streaming 时代的 choices[0].message.content，只拼接后者
                if delta.get('content'):
                    content_parts.append(delta['content'])
        except requests.RequestException as e:
            raise LLMError(f'流式响应中断: {e}') from e
        finally:
            response.close()

        answer = ''.join(content_parts)
        if not answer.strip():
            raise LLMError(f'流式响应结束但未收到回答内容（首行样例: {first_line!r}）')
        return answer


def create_client(config):
    """从配置创建当前 active provider 的 LLM 客户端

    抛出: ConfigError 当 active provider 缺失或 api_key 未填写时
    """
    active, provider = validate_llm_config(config)

    for field in ('base_url', 'model'):
        if not provider.get(field):
            raise ConfigError(f"provider {active!r} 缺少 {field} 配置")

    api_key = _resolve_api_key(active, provider)
    if not api_key or api_key == SAMPLE_API_KEY:
        raise ConfigError(
            f"provider {active!r} 未配置 api_key："
            f"请在 config.yaml 中填写，或设置环境变量 {active.upper()}_API_KEY"
        )

    return LLMClient(
        base_url=provider['base_url'],
        api_key=api_key,
        model=provider['model'],
        timeout=provider.get('timeout', 25),
        temperature=provider.get('temperature', 1.0),
        top_p=provider.get('top_p', 1.0),
        retries=provider.get('retries', 5),
    )


def ensure_config():
    """加载配置并构建 LLM 客户端；配置有问题时打印原因并退出

    为什么直接 sys.exit：本项目是单脚本工具，配置不完整时无法继续，
    启动阶段直接退出比让错误层层传递更直接
    返回: (config dict, LLMClient)
    """
    try:
        config = load_config()
        client = create_client(config)
        return config, client
    except ConfigError as e:
        print(f'配置错误: {e}')
        sys.exit(1)

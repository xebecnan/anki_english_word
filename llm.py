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
1. ${VAR} 显式环境变量引用
2. {PROVIDER大写}_API_KEY 环境变量（如 DEEPSEEK_API_KEY，兼容旧版行为）
3. 配置文件中的字面值
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
    """
    if isinstance(value, str):
        def replace(match):
            var = match.group(1)
            env = os.environ.get(var)
            if env is None:
                print(f'警告: 环境变量 {var} 未定义，已展开为空字符串')
                return ''
            return env
        return _ENV_VAR_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value


def _resolve_api_key(provider_name, provider_conf):
    """按优先级解析 api_key（见模块 docstring 的优先级说明）"""
    # 1) ${VAR} 显式引用（_expand_env_vars 已展开，这里只需判断是否残留未展开形式）
    key = provider_conf.get('api_key', '')

    # 2) {PROVIDER}_API_KEY 环境变量优先于配置文件字面值
    #    为什么环境变量优先：密钥不入库更安全，且兼容旧版 DEEPSEEK_API_KEY 的行为
    env_key = os.environ.get(f'{provider_name.upper()}_API_KEY')
    if env_key:
        return env_key

    return key


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
            'stream': False,
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
        """单次请求，任何失败都抛 LLMError（由 chat 统一重试）"""
        try:
            response = requests.post(
                url=self._build_url(),
                headers=headers,
                json=payload,
                stream=False,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise LLMError(f'网络请求失败: {e}') from e

        try:
            answer = response.json()
        except ValueError as e:
            raise LLMError(
                f'返回内容不是 JSON (HTTP {response.status_code}): {response.text[:200]}'
            ) from e

        # API 层错误（key 无效、余额不足等）：HTTP 200 但 body 里有 error 字段
        if 'error' in answer:
            message = answer['error'].get('message', answer['error']) \
                if isinstance(answer['error'], dict) else answer['error']
            raise LLMError(f'LLM API 错误: {message}')

        if response.status_code != 200:
            raise LLMError(f'HTTP {response.status_code}: {response.text[:200]}')

        try:
            return answer['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f'返回缺少 choices[0].message.content: {answer}') from e


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

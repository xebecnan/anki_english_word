# Anki 英语单词卡片生成器

自动批量生成高质量 Anki 英语学习卡片的工具。

## 功能特点

- **智能释义生成**: 使用可配置的 LLM API（DeepSeek / OpenAI / Kimi / 中转站等）为单词生成专业的音标、多义词解释和地道例句
- **自动音频获取**: 从有道词典或 Google TTS 自动下载单词发音
- **批量添加卡片**: 通过 Anki-Connect API 自动将卡片添加到 Anki 牌组
- **填空式学习**: 生成的卡片使用 Anki 填空（Cloze）格式，便于记忆
- **单词辨析模式**: 对比相似单词的用法差异，生成辨析卡片

## 工作原理

```
wordlist.yaml → LLM API (可配置 provider) → 单词释义(JSON)
                   ↓
              有道/Google TTS → 发音(MP3)
                   ↓
              Anki-Connect → Anki 卡片
```

1. 从 `wordlist.yaml` 读取待处理的单词列表
2. 调用 LLM API 获取单词的详细信息（释义、音标、5个例句）
3. 从有道词典或 Google TTS 下载单词发音
4. 通过 Anki-Connect API 将音频和卡片信息上传到 Anki

## 安装

需要 Python 3.11+，以及安装并运行 [Anki](https://apps.ankiweb.net/) 和 [Anki-Connect](https://ankiweb.net/shared/info/2055492159) 插件。

推荐在虚拟环境中以可编辑模式安装（依赖由 `pyproject.toml` 自动装齐，同时获得 `anki-english-word` 命令行入口）：

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
```

Windows 下装好后可直接运行仓库里的 `anki.bat` 启动（内部调用 `.venv\Scripts\anki-english-word.exe`，可以带参数透传）。

不想创建虚拟环境的话，也可以手动安装依赖后直接 `python main.py` 运行：

```bash
pip install requests beautifulsoup4 gTTS pyyaml
```

## 配置

首次运行时，程序会自动生成 `config.yaml` 模板；若检测到旧版 `config.json` 则自动迁移为 `config.yaml`（原文件保留）。

LLM 服务商通过配置切换（主流服务商均兼容 OpenAI 协议，`base_url` 填根路径即可）：

```yaml
llm:
  active: deepseek              # 当前使用的 provider，切换服务商改这一行
  providers:
    deepseek:
      base_url: https://api.deepseek.com
      model: deepseek-chat
      api_key: sk-xxx
    openai:
      base_url: https://api.openai.com/v1
      model: gpt-4o-mini
      api_key: ${OPENAI_API_KEY}   # 支持 ${VAR} 环境变量引用
    relay:                         # 第三方中转站示例
      base_url: https://api.ohmygpt.com/v1
      model: gpt-3.5-turbo
      api_key: sk-xxx

# 网络代理：仅用于 Google TTS 等境外服务，不影响 LLM 请求
proxies:
  http: http://localhost:8123
  https: http://localhost:8123
```

更多 provider 示例和可选参数（timeout / temperature / retries）见 `config.yaml.example`。

### api_key 的优先级

1. 环境变量 `{PROVIDER大写}_API_KEY`（如 `DEEPSEEK_API_KEY`、`OPENAI_API_KEY`，兼容旧版行为）
2. 配置文件中的 api_key（其中 `${VAR}` 形式的引用在加载配置时展开为环境变量的值，之后与字面值等价；引用的变量未定义时解析为空并警告）

## 使用方法

### 1. 创建单词列表

在 `wordlist.yaml` 中添加要学习的单词（可参考 `wordlist.yaml.example`）：

```yaml
# wordlist.yaml
en:
  default:
    - savvy
    - tribulation
    - epiphany
  名词:
    - dough

zh:
  default:
    - 你好
```

- **顶层 key**: 语言代码（如 `en`, `zh`），用于 Google TTS 发音
- **第二层 key**: 单词类型（如 `default`, `名词`, `compare`）
  - `default`: 使用标准卡片模板（ShuffledCloze）
  - `名词`: 使用简化的正反面卡片模板
  - `compare`: 单词辨析模式（见下文）
- **单词列表**: 每个类型下是一个字符串数组

### 单词辨析模式

用于对比相似单词的用法差异。在配置中添加 `compare` 节点：

```yaml
en:
  default:
    - savvy

  # 辨析模式：每组至少2个单词
  compare:
    - [fission, fissure]
    - [predicament, plight]
    - [affect, effect, impact]  # 支持超过2个单词
```

辨析卡片使用 Anki 的「填空题」（Cloze）模型，字段为 `文字` 和 `Back Extra`：

**正面（文字）**:
```
Nuclear {{c1::fission}} is a process that releases enormous amounts of energy.
( fission / fissure )
```

例句中答案词被挖空，根据括号里的候选词作答。

**背面（Back Extra）**:
```
答案: fission

翻译：核裂变是一个释放巨大能量的过程。

[sound:fission.mp3]

- fission：名词，指分裂、裂变（核裂变、细胞分裂），与句子语境完美匹配
- fissure：名词，指裂缝、裂隙，与 nuclear 搭配不自然（显示为删除线）
```

运行辨析模式：
```bash
# 自动检测 compare 节点并运行
python main.py

# 或显式指定
python main.py -c
```

### 2. 运行程序

用 `pip install -e .` 安装后可直接用 `anki-english-word` 命令代替 `python main.py`，参数一致。

```bash
# 完整流程：生成释义、下载发音、添加卡片
python main.py

# 只获取单词释义（不添加卡片）
python main.py -i

# 只下载发音
python main.py -s

# 下载并上传发音到 Anki
python main.py -S

# 强制重新处理（跳过已存在的单词）
python main.py -f

# 使用 Google TTS 代替有道发音
python main.py -g
```

### 命令行参数

| 参数 | 说明 |
|------|------|
| `-f, --force` | 强制重新获取已存在单词的信息 |
| `-s, --sound-only` | 只获取发音，不生成释义和添加卡片 |
| `-S, --store-sound` | 获取发音并上传到 Anki |
| `-g, --google-sound` | 使用 Google TTS 获取发音（默认使用有道） |
| `-i, --info-only` | 只获取单词释义，不添加卡片 |
| `-c, --compare` | 单词辨析模式 |

## 卡片格式

### 普通单词（ShuffledCloze 模型）

使用填空格式，单词在例句中高亮：

```json
{
    "word": "savvy",
    "pronunciation": "/ˈsævi/",
    "definition": [
        "n. 实际知识，见识，悟性",
        "adj. 精明的，有见识的",
        "v. 理解，懂（非正式用法）"
    ],
    "example1": "She has impressive business {{c1::savvy}}...",
    "example2": "To succeed, you need to be financially {{c1::savvy}}...",
    ...
}
```

### 名词（基础模型）

使用正反面格式：

```
正面: 例句
背面: 例句翻译
详情: 单词 意思 音标
音频: [sound:word.mp3]
```

## 目录结构

```
.
├── main.py                      # 主程序
├── llm.py                       # LLM 访问模块（配置加载 + OpenAI 兼容客户端）
├── pyproject.toml               # 项目元数据与依赖声明（pip install -e . 的入口）
├── anki.bat                     # Windows 便捷启动脚本（调用 .venv 中的命令行入口）
├── prompt_1.txt                 # AI 提示词模板（名词）
├── prompt_2.txt                 # AI 提示词模板（通用）
├── prompt_compare_sentences.txt # AI 提示词模板（辨析-例句生成）
├── prompt_compare_analysis.txt  # AI 提示词模板（辨析-句子分析）
├── config.yaml.example          # 配置文件模板（多 provider 示例）
├── config.yaml                  # 配置文件（自动生成，需填 api_key）
├── wordlist.yaml.example        # 单词列表示例
├── wordlist.yaml                # 单词列表（参考 example 创建）
├── sound/                       # 音频缓存目录（运行时生成）
├── new_info/                    # 待添加的单词信息（运行时生成）
└── archived/                    # 已添加到 Anki 的单词归档（运行时生成）
```

## 注意事项

1. **Anki 必须运行**: 程序通过 Anki-Connect 与 Anki 通信，请确保 Anki 正在运行且已安装 Anki-Connect 插件

2. **牌组名称**: 默认添加到 `English::Arnan's English Sentences` 牌组，可在 `main.py` 中修改 `deck_name` 变量

3. **API 配额**: LLM API 有调用限制，批量处理大量单词时请注意

4. **网络代理**: `config.yaml` 中的 `proxies` 仅用于 Google TTS 等境外服务；LLM API 请求不走代理

## License

MIT

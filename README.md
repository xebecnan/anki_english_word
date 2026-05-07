# Anki 英语单词卡片生成器

自动批量生成高质量 Anki 英语学习卡片的工具。

## 功能特点

- **智能释义生成**: 使用 AI (DeepSeek) 为单词生成专业的音标、多义词解释和地道例句
- **自动音频获取**: 从有道词典或 Google TTS 自动下载单词发音
- **批量添加卡片**: 通过 Anki-Connect API 自动将卡片添加到 Anki 牌组
- **填空式学习**: 生成的卡片使用 Anki 填空（Cloze）格式，便于记忆
- **单词辨析模式**: 对比相似单词的用法差异，生成辨析卡片

## 工作原理

```
wordlist.yaml → DeepSeek API → 单词释义(JSON)
                   ↓
              有道/Google TTS → 发音(MP3)
                   ↓
              Anki-Connect → Anki 卡片
```

1. 从 `wordlist.yaml` 读取待处理的单词列表
2. 调用 DeepSeek API 获取单词的详细信息（释义、音标、5个例句）
3. 从有道词典或 Google TTS 下载单词发音
4. 通过 Anki-Connect API 将音频和卡片信息上传到 Anki

## 安装依赖

```bash
pip install requests beautifulsoup4 gTTS pyyaml
```

还需要安装并运行 [Anki](https://apps.ankiweb.net/) 以及 [Anki-Connect](https://ankiweb.net/shared/info/2055492159) 插件。

## 配置

首次运行时，程序会自动创建 `config.json` 配置文件：

```json
{
    "API_KEY": "your-deepseek-api-key",
    "PROXIES": {
        "http": "http://localhost:8123",
        "https": "http://localhost:8123"
    }
}
```

也可以通过环境变量 `DEEPSEEK_API_KEY` 设置 API 密钥。

## 使用方法

### 1. 创建单词列表

在 `wordlist.yaml` 中添加要学习的单词：

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

辨析卡片格式：

**正面**:
```
Nuclear ___ is a process that releases enormous amounts of energy. ( fission / fissure )
```

**背面**:
```
答案: fission

翻译：核裂变是一个释放巨大能量的过程。

[sound:fission.mp3]

- fission：名词，指分裂、裂变（核裂变、细胞分裂），与句子语境完美匹配
- fissure：名词，指裂缝、裂隙，与 nuclear 搭配不自然
```

运行辨析模式：
```bash
# 自动检测 compare 节点并运行
python main.py

# 或显式指定
python main.py -c
```

### 2. 运行程序

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
├── prompt_1.txt                 # AI 提示词模板（名词）
├── prompt_2.txt                 # AI 提示词模板（通用）
├── prompt_compare_sentences.txt # AI 提示词模板（辨析-例句生成）
├── prompt_compare_analysis.txt  # AI 提示词模板（辨析-句子分析）
├── config.json                  # 配置文件（API 密钥、代理）
├── wordlist.yaml                # 单词列表（需自己创建）
├── sound/                       # 音频缓存目录
├── new_info/                    # 待添加的单词信息
└── archived/                    # 已添加到 Anki 的单词归档
```

## 注意事项

1. **Anki 必须运行**: 程序通过 Anki-Connect 与 Anki 通信，请确保 Anki 正在运行且已安装 Anki-Connect 插件

2. **牌组名称**: 默认添加到 `English::Arnan's English Sentences` 牌组，可在 `main.py` 中修改 `deck_name` 变量

3. **API 配额**: DeepSeek API 有调用限制，批量处理大量单词时请注意

4. **网络代理**: 如需使用代理访问 API，请在 `config.json` 中配置

## 开发

详细开发文档请参考项目内的 `docs/` 目录。

## License

MIT

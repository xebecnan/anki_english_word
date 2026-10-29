# coding: utf-8

import os
import sys
import requests
import json
from bs4 import BeautifulSoup
import base64
import html
import time
import random
import argparse
from gtts import gTTS
import yaml

import llm

# 启动即加载配置并构建 LLM 客户端；配置缺失/非法时打印原因并退出
# 之所以在模块级做：原实现也是模块级读配置，保持行为一致，
# 后续所有调用点都能直接用 LLM_CLIENT，不用层层传参
CONFIG, LLM_CLIENT = llm.ensure_config()

# 代理仅用于 gTTS 等需要访问 Google 的服务；LLM API 请求不走代理
# 允许缺节点：不配置代理时 gTTS 直连（连不上会在重试后走有道/报错）
PROXIES = CONFIG.get('proxies') or {}

REQUIRED_TAGS = ('单词', '意思', '音标', '例句', '例句翻译')
ARCHIVED_DIR = 'archived'

# 非默认词条类型 → prompt 模板文件的映射，未命中的走通用单词模板 prompt_2.txt。
# 词组（短语动词/搭配）与单个单词的认知重点不同：学习者通常认识其中每个单词，
# 缺的是「什么场景用、可分性、小品词重音」，所以用专属模板。
# JSON 字段结构与单词一致，卡片仍复用 ShuffledCloze，差异只在内容生成层
PROMPT_FILE_BY_TYPE = {
    '词组': 'prompt_phrase.txt',
}

# 补位重试上限：某个单词的例句未通过校验时，最多重新生成多少「批」新例句去补位，
# 仍补不齐则接受减量（宁可少几张卡，也不硬塞质量可疑的句子）
# 按「批」而不是按「槽位」计数：例句被废往往是系统性的（如近义词组中
# 较少见词的例句总被判成更常见的近义词），按槽位重试会反复生成注定
# 失败的批次，LLM 调用成倍浪费
MAX_EXAMPLE_RETRY = 2



def load_info(info, word):
    parsed = { '单词': word }
    for line in info.splitlines():
        if '：' in line:
            tag, body = line.strip().split('：', 1)
            parsed[tag] = body
    return parsed


def check_info(info, word):
    parsed = load_info(info, word)
    for tag in REQUIRED_TAGS:
        if tag not in parsed:
            print(f'PARSE ERROR! tag:{tag} word:{word}')
            print('-----------------------------------')
            print(info)
            print('===================================')
            return False
    return True


def extract_valid_json_string(text):
    s = text.find('{')
    e = text.rfind('}')
    json_string = text[s:e+1]
    try:
        info = json.loads(json_string)
    except Exception as e:
        return None

    req_fields = ['word', 'pronunciation', 'definition', 'example1', 'example2', 'example3', 'example4', 'example5']
    for f in req_fields:
        if f not in info:
            return None
    return json_string


def get_word_info_new(word, word_type=None):
    prompt_file = PROMPT_FILE_BY_TYPE.get(word_type, 'prompt_2.txt')
    with open(prompt_file, 'r', encoding='utf-8') as f:
        prompt = f.read() + word
    # 重试已下沉到 LLMClient.chat 内部，这里只需捕获「重试耗尽」的失败
    # （原实现在重试耗尽后使用未定义的 gpt_answer 会直接 NameError）
    try:
        gpt_answer = LLM_CLIENT.chat(prompt)
    except llm.LLMError as e:
        print(f'get_word_info_new failed: {e}')
        return None

    json_string = extract_valid_json_string(gpt_answer)
    if not json_string:
        print(f'PARSE ERROR!')
        print('-----------------------------------')
        print(gpt_answer)
        print('===================================')
    return json_string

def get_word_info_for_noun(word):
    prompt = f'''单词：{word}
意思：____
音标：/____/
例句：____
例句翻译：____'''
    try:
        info = LLM_CLIENT.chat(prompt)
    except llm.LLMError as e:
        print(f'get_word_info_for_noun failed: {e}')
        return None

    return check_info(info, word) and info or None


def get_word_list():
    """从 wordlist.yaml 读取单词列表

    YAML 格式：
        en:
          default:
            - savvy
            - tribulation
          名词:
            - dough
        zh:
          default:
            - 你好

    返回: [(word, word_type, word_lang), ...]
    """
    words = []
    with open('wordlist.yaml', 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}

    for lang, types in data.items():
        if not isinstance(types, dict):
            continue
        for word_type, word_list in types.items():
            # 跳过 compare 节点
            if word_type == 'compare':
                continue
            if not isinstance(word_list, list):
                continue
            for word in word_list:
                if not word or not isinstance(word, str):
                    continue
                word = word.strip()
                if word and not word.startswith('#'):
                    words.append((word, word_type, lang))

    return words


def get_compare_groups():
    """从 wordlist.yaml 读取辨析组

    YAML 格式：
        en:
          compare:
            - [fission, fissure]
            - [predicament, plight]

    返回: [(word_list, lang), ...]
    例如: [(['fission', 'fissure'], 'en'), ...]
    """
    groups = []
    with open('wordlist.yaml', 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}

    for lang, sections in data.items():
        if not isinstance(sections, dict):
            continue
        compare_section = sections.get('compare', [])
        if not isinstance(compare_section, list):
            continue
        for word_list in compare_section:
            if isinstance(word_list, list) and len(word_list) >= 2:
                # 过滤空值和非字符串
                valid_words = [w.strip() for w in word_list if w and isinstance(w, str)]
                if len(valid_words) >= 2:
                    groups.append((valid_words, lang))

    return groups


def generate_comparison_sentences(word_list, focus=None):
    """为一组单词生成例句（每词5句）

    focus 不为空时表示「补位重试」场景：只为该词生成例句，
    但把组内其余词作为辨析参照一并列出，让新例句针对近义词做语境区分，
    而不是再产出“任何近义词都能填”的中性句子。

    返回: dict, 格式如:
        {
            "fission": {
                "definition": "名词，指分裂、裂变",
                "examples": ["Nuclear ___ is...", ...]
            },
            ...
        }
    """
    with open('prompt_compare_sentences.txt', 'r', encoding='utf-8') as f:
        prompt_template = f.read()

    if focus:
        # 补位场景：单词列表只列目标词，组内其余词放进「辨析参照」节，
        # 让模型明确知道竞争者是谁，而不是闷头生成“典型用法”
        words_str = f'- {focus}'
        siblings = [w for w in word_list if w != focus]
        sibling_lines = '\n'.join(f'- {w}' for w in siblings)
        focus_section = (
            '## 同组近义词（辨析参照，不要为它们生成例句）\n'
            f'{sibling_lines}\n\n'
            f'本次只需为 {focus} 生成例句，输出 JSON 只包含 "{focus}" 这一个键。\n'
            f'生成每个例句时都要与上面的近义词对比，确保在该句的语域、搭配和语境下，'
            f'{focus} 是比它们更贴切的最佳选择。\n\n'
        )
    else:
        words_str = '\n'.join([f'- {w}' for w in word_list])
        focus_section = ''

    prompt = prompt_template \
        .replace('{words}', words_str) \
        .replace('{focus_section}', focus_section)

    try:
        gpt_answer = LLM_CLIENT.chat(prompt)
    except llm.LLMError as e:
        print(f'generate_comparison_sentences failed: {e}')
        return None

    # 提取 JSON
    s = gpt_answer.find('{')
    e = gpt_answer.rfind('}')
    json_string = gpt_answer[s:e+1]

    try:
        result = json.loads(json_string)
        return result
    except Exception as e:
        print(f'PARSE ERROR in generate_comparison_sentences: {e}')
        print(gpt_answer)
        return None


def analyze_sentence(sentence, candidate_words):
    """盲测式分析句子中词汇替换的可能性

    为什么不把「目标词」单独标出来：例句是为某个词量身生成的，如果把目标词
    单独传给 LLM，它会锚定在目标词上倾向确认而非证伪，辨析就失去了独立性。
    这里只传打乱顺序的全部候选词，让 LLM 独立判断哪个最合适；
    判断结果与生成意图是否一致，由调用方（make_comparison_cards）校验——
    不一致说明例句本身没有体现目标词的独特用法，应丢弃而非硬选。

    不变量：answer 是唯一答案（即使多词可行，也只选语境下最地道的一个，
    其余词在 explanation 中如实说明可行程度）。

    返回: dict, 格式如:
        {
            "answer": "fission",
            "translation": "核裂变是一个释放巨大能量的过程。",
            "analysis": [
                {"word": "fission", "is_correct": True, "explanation": "..."},
                {"word": "fissure", "is_correct": False, "explanation": "..."}
            ]
        }
    """
    with open('prompt_compare_analysis.txt', 'r', encoding='utf-8') as f:
        prompt_template = f.read()

    # 打乱候选词顺序，消除 LLM 偏向列表靠前位置词的位置偏差
    words = list(candidate_words)
    random.shuffle(words)
    words_str = ', '.join(words)
    prompt = prompt_template \
        .replace('{sentence}', sentence) \
        .replace('{words}', words_str)

    try:
        gpt_answer = LLM_CLIENT.chat(prompt)
    except llm.LLMError as e:
        print(f'analyze_sentence failed: {e}')
        return None

    # 提取 JSON
    s = gpt_answer.find('{')
    e = gpt_answer.rfind('}')
    json_string = gpt_answer[s:e+1]

    try:
        result = json.loads(json_string)
        return result
    except Exception as e:
        print(f'PARSE ERROR in analyze_sentence: {e}')
        print(gpt_answer)
        return None


def build_comparison_card(sentence, analysis_result, all_words, audio_url):
    """构建辨析卡片（使用「填空题」Cloze 模型）

    正面（文字字段）：例句中答案词标记为 {{c1::...}}，下方一行列出候选词
    背面（Back Extra 字段）：答案、翻译、发音、逐词辨析

    返回: dict, 包含 '文字' 和 'Back Extra' 字段
    """
    def esc(text):
        # LLM 返回的文本可能包含 <、& 等字符，不转义会破坏 HTML 排版
        return html.escape(str(text))

    answer = analysis_result.get('answer', '')

    # 填空题模型要求文字字段中存在 {{c1::...}} 标记，否则 Anki 会报“没有找到填空”错误：
    # 把例句中的 ___ 替换为答案词的 cloze 标记
    front = sentence.replace('___', '{{c1::' + esc(answer) + '}}')
    # 候选项单独一行显示；Anki 不渲染裸 '\n'，用 <br> 换行
    options_str = ' / '.join(esc(w) for w in all_words)
    front = f'{front}<br>( {options_str} )'

    # 构建 Back Extra
    translation = analysis_result.get('translation', '')
    analysis = analysis_result.get('analysis', [])

    # Anki 字段按 HTML 渲染，裸换行符 '\n' 会被折叠成一个空格（整段挤成一行），
    # 因此用块级 <div> 分行、<ul>/<li> 呈现辨析列表；
    # 区块间距用内联 margin，避免依赖模板的 CSS

    back_parts = [
        f'<div>答案: <b>{esc(answer)}</b></div>',
        f'<div style="margin-top: 0.4em">翻译：{esc(translation)}</div>',
        f'<div style="margin-top: 0.4em">[sound:{audio_url}]</div>',
    ]

    analysis_items = []
    for item in analysis:
        word = item.get('word', '')
        explanation = item.get('explanation', '')
        # 仅在 LLM 明确标记为错误时给词加删除线；
        # 字段缺失时保持原样，避免把答案词也划掉
        if item.get('is_correct') is False:
            word = f'<s>{esc(word)}</s>'
        else:
            word = esc(word)
        analysis_items.append(f'<li>{word}：{esc(explanation)}</li>')

    if analysis_items:
        back_parts.append('<ul>' + ''.join(analysis_items) + '</ul>')

    back = ''.join(back_parts)

    return {
        '文字': front,
        'Back Extra': back,
    }


def make_one_comparison_card(example, word, word_list, label=''):
    """处理单个例句：分析 → 校验 → 构卡 → 入 Anki

    返回 (True, None)：成功入库
    返回 (False, reason)：例句内容有问题被废弃，reason 为简短原因，
        可以通过重新生成例句来补位
    返回 (False, None)：Anki 入库失败（环境问题），换例句重试没有意义
    """
    if not example or '___' not in example:
        print(f'    {label}格式不正确，跳过')
        return False, '格式不正确（缺少 ___ 占位符）'

    # 盲测分析：不告知目标词，让 LLM 独立判断
    analysis = analyze_sentence(example, word_list)
    if not analysis:
        # 打印原句：分析失败常见于例句触发了服务商的内容审核（如涉暴力、
        # 镇压类语境），留下原句才能定位是哪句话、要不要改写
        print(f'    {label}分析失败，跳过：{example}')
        return False, '分析失败'

    # 提示词已要求 answer 唯一，但 LLM 可能不遵守，这里兜底校验：
    # 1) answer 必须是词组内的单词，防止幻觉词/空值进卡片
    answer = str(analysis.get('answer', '')).strip()
    if answer not in word_list:
        print(f'    {label}答案 {answer!r} 不在词组 {word_list} 内，跳过')
        return False, f'答案 {answer!r} 不在词组内'
    analysis['answer'] = answer

    # 2) 盲测答案应与生成意图（目标词）一致；不一致说明例句没有
    #    真正体现目标词的独特用法，丢弃比硬选更有价值，
    #    同时避免了「答案与背面发音错位」的问题（音频按目标词取）
    if answer != word:
        print(f'    {label}盲测答案 {answer!r} 与目标词 {word!r} 不一致，丢弃该句')
        return False, f'盲测答案 {answer!r} 与目标词不一致'

    # 3) 强制「answer 唯一」不变量，避免卡片自相矛盾；
    #    解析文字仍保留「可行度相同」等说明，只是视觉上只标一个标准答案
    for item in analysis.get('analysis', []):
        if item.get('word') == answer:
            if not item.get('is_correct'):
                print(f'    {label}LLM 将答案词标为错误，已修正')
                item['is_correct'] = True
        elif item.get('is_correct'):
            print(f'    {label}LLM 标记了多个可行词，已将 {item.get("word")} 归为非答案')
            item['is_correct'] = False

    # 上传音频
    audio_url = anki_media_exist_for_word(word) or upload_mp3_for_card(word)

    # 构建卡片
    card = build_comparison_card(example, analysis, word_list, audio_url)

    # 添加到 Anki（使用「填空题」Cloze 模型）
    return add_anki_card(card, '填空题') and (True, None) or (False, None)


def fill_failed_slots(pending_retries, word_list, max_retry):
    """补位失败槽位：按单词分组重新生成例句，用新例句尽量填回所有失败槽位

    为什么按单词分组而不是每个失败槽位各自重试：
    例句被废往往是系统性的（同一个词的例句总因同样的原因不达标），
    按槽位重试会为每个槽位各生成一批注定失败的例句；按词分组后，
    每个词的重试批次数有上限（max_retry 批），一批新例句也顺带能补多个槽位。

    补位生成时把整个词组传给模型（focus 指定目标词），让新例句针对
    组内近义词做语境区分；否则只会再产出一批“谁都能填”的中性句。

    返回: (filled, gaveup)
        filled: 成功补位的例句数
        gaveup: True 表示 Anki 入库失败（环境问题），继续换例句重试没有意义
    """
    need_by_word = {}
    for word, _reason in pending_retries:
        need_by_word[word] = need_by_word.get(word, 0) + 1

    filled = 0
    for word, need in need_by_word.items():
        remaining = need
        for attempt in range(1, max_retry + 1):
            print(f'  补位 {word}（还差 {remaining} 句），第 {attempt}/{max_retry} 批重新生成例句...')
            retry_data = generate_comparison_sentences(word_list, focus=word)
            if not retry_data:
                continue

            for example in retry_data.get(word, {}).get('examples', []):
                ok, retry_reason = make_one_comparison_card(example, word, word_list, label='补位例句 ')
                if ok:
                    remaining -= 1
                    if remaining == 0:
                        break
                elif retry_reason is None:
                    print('    补位例句入库失败（Anki 环境问题）')
                    return filled, True
            if remaining == 0:
                break  # 该词所有槽位已补齐，无需再生成新批次
            # 一批新例句没能补齐剩余槽位，进入下一批（或耗尽后接受减量）

        if remaining > 0:
            print(f'  补位失败: {word}（{max_retry} 批新例句仍未补齐，缺 {remaining} 句），接受减量')
        filled += need - remaining

    return filled, False


def make_comparison_cards(force, use_google_sound):
    """生成辨析卡片的主流程"""
    groups = get_compare_groups()
    if not groups:
        print('没有找到辨析组配置')
        return

    print(f'找到 {len(groups)} 个辨析组')

    total_cards = 0
    for group_idx, (word_list, lang) in enumerate(groups):
        print(f'\n=== 辨析组 {group_idx + 1}/{len(groups)}: {word_list} ===')

        # 已处理过的组直接跳过：默认模式每次都会串行跑到这里，
        # 没有这层防重就会重复调 LLM 并重复建卡（-f 强制重跑）
        if not force and os.path.exists(get_compare_done_path(word_list)):
            print('SKIP: 该辨析组已处理过（-f 强制重跑）')
            continue

        # 1. 为每个单词下载发音
        for word in word_list:
            if not sound_exist_for_word(word):
                download_mp3_for_word(word, lang, use_google_sound)

        # 2. 生成例句
        print(f'生成例句...')
        sentences_data = generate_comparison_sentences(word_list)
        if not sentences_data:
            print(f'生成例句失败，跳过该组')
            continue

        # 3. 遍历每个单词的每个例句，生成卡片
        group_cards = 0
        target_cards = 0
        pending_retries = []  # 内容不合格被废弃的例句，按 (word, 原因) 记录待补位
        for word in word_list:
            word_data = sentences_data.get(word, {})
            examples = word_data.get('examples', [])

            print(f'  处理单词 {word}，共 {len(examples)} 个例句')
            target_cards += len(examples)

            for ex_idx, example in enumerate(examples):
                ok, reason = make_one_comparison_card(example, word, word_list, label=f'例句 {ex_idx + 1} ')
                if ok:
                    group_cards += 1
                    print(f'    例句 {ex_idx + 1} → 卡片已添加')
                elif reason is None:
                    print(f'    例句 {ex_idx + 1} 添加失败')
                else:
                    pending_retries.append((word, reason))

        # 4. 补位重试：废弃的例句用该词的新例句补齐，仍失败则接受减量
        if pending_retries:
            print(f'\n  {len(pending_retries)} 个例句未通过校验，开始补位重试（每个单词最多 {MAX_EXAMPLE_RETRY} 批新例句）')

        filled, gaveup = fill_failed_slots(pending_retries, word_list, MAX_EXAMPLE_RETRY)
        group_cards += filled
        if gaveup:  # Anki 环境问题，重试无意义，不再浪费 LLM 调用
            print('  放弃其余补位（Anki 入库失败，重试无意义）')

        total_cards += group_cards
        print(f'  本组完成: {group_cards}/{target_cards} 张卡片')

        # 组正常走完（含接受减量）才标记已处理；Anki 入库失败（gaveup）
        # 时不标记，修复环境后重跑可补齐缺失卡片——代价是组内已成功
        # 部分会重复建卡，输出里已提醒
        if not gaveup:
            done_path = get_compare_done_path(word_list)
            os.makedirs(os.path.dirname(done_path), exist_ok=True)
            with open(done_path, 'w', encoding='utf-8') as f:
                f.write(' '.join(word_list))
        else:
            print('  注意: 本组未标记为已处理（Anki 入库失败），修复后重跑可补齐，但组内已成功部分会重复建卡')

    print(f'\n完成！共添加 {total_cards} 张辨析卡片')


def sanitize_name(word):
    """把词条转成文件名安全的形式

    词组（如 drift off）含空格，而 Anki 的 [sound:...] 引用和媒体探测
    对带空格的文件名不可靠，统一转成下划线。
    只影响磁盘/媒体库文件名；卡片内容里显示的词条保持原样。
    """
    return word.replace(' ', '_')


def get_new_info_path_for_word(word):
    return os.path.join('new_info', sanitize_name(word))


def get_archived_path_for_word(word):
    return os.path.join(ARCHIVED_DIR, time.strftime("%Y-%m"), sanitize_name(word))


def get_compare_done_path(word_list):
    """辨析组的「已处理」标记文件路径

    辨析流程没有单词那样的 new_info→archived 生命周期（例句每次都重新
    生成，没有稳定的中间产物可归档），所以组正常走完后写一个标记文件。
    为什么需要：默认模式串行处理所有待办（普通类型 + 辨析组），而辨析组
    重复运行会重新调 LLM 并重复建卡，必须防重。按组而非按单词标记，
    因为组内单词的例句是互相参照生成的，部分重跑会破坏辨析语境
    """
    name = 'compare_' + '_'.join(sanitize_name(w) for w in word_list)
    return os.path.join(ARCHIVED_DIR, time.strftime("%Y-%m"), name)


def is_word_archieved(word):
    for name in os.listdir(ARCHIVED_DIR):
        path = os.path.join(ARCHIVED_DIR, name)
        if not os.path.isdir(path):
            continue
        word_path = os.path.join(path, sanitize_name(word))
        if not os.path.isfile(word_path):
            continue
        return True
    return False


def get_mp3_path_for_word(word):
    return os.path.join('sound', f"{sanitize_name(word)}.mp3")


def already_have_info_for_word(word):
    if os.path.exists(get_new_info_path_for_word(word)):
        return True
    if is_word_archieved(word):
        return True
    return False


def save_word_info(word, info):
    path = get_new_info_path_for_word(word)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(info)


def load_word_info(word):
    path = get_new_info_path_for_word(word)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def get_pronunciation_mp3(word):
    # Set the URL for the Youdao online dictionary search
    url = f"https://dict.youdao.com/dictvoice?audio={word}&type=2"

    # Make a GET request to the audio URL and return the binary content
    for retry in range(5):
        if retry > 0:
            print(f'Retrying... sleep for {2**retry} seconds')
            time.sleep(2 ** retry)
        try:
            response = requests.get(url)
            if response.ok:
                return response.content
            else:
                print(f'Request failed with status {response.status_code}, retry {retry + 1}')
        except Exception as e:
            print(f'Request failed with exception: {e}, retry {retry + 1}')
    return None


def download_mp3_for_word(word, lang, use_google_sound):
    print(f'fetching MP3: {word}')
    filepath = get_mp3_path_for_word(word)
    # sound/ 目录可能尚不存在（如新 clone 后直接用 -r 重取单个单词），
    # 完整添加流程会被更早的步骤隐式创建，这里显式保证一下
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    # 尝试从有道获取
    if lang == 'en' and not use_google_sound:
        mp3_content = get_pronunciation_mp3(word)
        if mp3_content:
            with open(filepath, "wb") as f:
                f.write(mp3_content)
            return

        print(f'DOWNLOADED MP3 FAILED {word}')
        print('try google tts')

    # 尝试从 google translate 获取
    # gTTS 内部用 requests 下载，requests 会读 HTTP_PROXY/HTTPS_PROXY 环境变量；
    # 不配置代理时直接连（避免把空值写进环境变量）
    use_proxy = bool(PROXIES.get('http'))
    if use_proxy:
        assert 'HTTP_PROXY' not in os.environ
        assert 'HTTPS_PROXY' not in os.environ
        os.environ['HTTP_PROXY'] = PROXIES['http']
        os.environ['HTTPS_PROXY'] = PROXIES.get('https') or PROXIES['http']
    gTTS(text=word, lang=lang, slow=False).save(filepath)
    if use_proxy:
        del os.environ['HTTP_PROXY']
        del os.environ['HTTPS_PROXY']


def fetch_and_save_info(word, word_type, force):
    if not force and already_have_info_for_word(word):
        return
    print(f'fetching info: {word}')
    if word_type == '名词':
        info = get_word_info_for_noun(word)
    else:
        info = get_word_info_new(word, word_type)
    if info:
        save_word_info(word, info)


def mp3_exist_for_word(word):
    filepath = get_mp3_path_for_word(word)
    return os.path.exists(filepath)


def sound_exist_for_word(word):
    if mp3_exist_for_word(word):
        return True

    if anki_media_exist_for_word(word):
        return True

    return False


def fetch_and_save_sound(word, word_lang, use_google_sound):
    if sound_exist_for_word(word):
        print('AUDIO ALREADY EXIST:', word)
        return
    download_mp3_for_word(word, word_lang, use_google_sound)


def add_anki_card(note_fields, model_name):
    # Set the URL for the Anki-Connect API
    url = "http://localhost:8765"

    # Set the action to add a new note
    action = "addNote"

    # Set the deck name to add the note to
    deck_name = "English::Arnan's English Sentences"

    # 模型名由调用方显式传入（单词卡用 ShuffledCloze/基础，辨析卡用填空题），
    # 字段结构与模型选择解耦，新增卡片类型时不用再改这里

    # # Set the note fields (front and back)
    # note_fields = {"正面": front, "Back": back}

    # Set the note tags (optional)
    # note_tags = ["tag1", "tag2"]

    # Construct the request payload as a Python dictionary
    request_payload = {
        "action": action,
        "version": 6,
        "params": {
            "note": {
                "deckName": deck_name,
                "modelName": model_name,
                "fields": note_fields
                # "tags": note_tags
            }
        }
    }

    # Convert the request payload to JSON format
    request_json = json.dumps(request_payload)

    # print(json.dumps(json.loads(request_json), indent=4))

    # Make a POST request to the Anki-Connect API with the request payload as the body
    response = requests.post(url, data=request_json)

    # print("response.text:", response.text)
    # Parse the response JSON and return the result
    response_json = json.loads(response.text)
    err = response_json.get('error', None)
    if err:
        print(f'add anki card error: {err}')
    return response_json["result"]


def build_anki_card(word, word_type, info, audio_url):
    print('info:', info)
    if word_type == '名词':
        parsed = load_info(info, word)
        return {
            '正面': parsed['例句'],
            '背面': parsed['例句翻译'],
            'Detail': f'{parsed["单词"]} {parsed["意思"]} {parsed["音标"]}',
            'Audio': f'[sound:{audio_url}]',
            'Sort Field': parsed["单词"],
            'QuestionHint': ''
        }

    info = json.loads(info)
    def_ul = ''.join([f'<li><sub>{d}</sub></li>' for d in info['definition']])
    card = {
        'Explain': '{{c1::' + info['word'] + '}}<br>' + f'<ul>{def_ul}</ul>',
        's1': info['example1'],
        's2': info['example2'],
        's3': info['example3'],
        's4': info['example4'],
        's5': info['example5'],
        'Back Extra': f'<b>{info["word"]}</b> {info["pronunciation"]}',
        'Sort Field': info['word'],
        'Audio': f'[sound:{audio_url}]',
    }
    print(card)
    return card


def file_to_base64(file_path):
    with open(file_path, "rb") as f:
        data = f.read()
    return base64.b64encode(data).decode("utf-8")


def check_media_for_file(filename):
    request_payload = {
        "action": "retrieveMediaFile",
        "version": 6,
        "params": {
            "filename": filename,
        }
    }

    # Make a POST request to the Anki-Connect API with the request payload as the body
    url = "http://localhost:8765"
    request_json = json.dumps(request_payload)
    response = requests.post(url, data=request_json)

    # Parse the response JSON and get the URL of the uploaded file
    response_json = json.loads(response.text)
    err = response_json.get('error', None)
    if response_json['result']:
        return filename
    else:
        return None


def anki_media_exist_for_word(word):
    filepath = get_mp3_path_for_word(word)
    for filename in [ '_EAUTO_' + os.path.basename(filepath), os.path.basename(filepath) ]:
        audio_url = check_media_for_file(filename)
        if audio_url:
            return audio_url
    return None


def upload_mp3_for_card(word, filename=None):
    # Construct the request payload as a Python dictionary
    filepath = get_mp3_path_for_word(word)
    # 默认用当前代码的 _EAUTO_ 前缀命名；重取音频时需要以卡片
    # 实际引用的旧文件名覆盖，所以允许调用方显式指定
    request_payload = {
        "action": "storeMediaFile",
        "version": 6,
        "params": {
            "filename": filename or ('_EAUTO_' + os.path.basename(filepath)),
            "data": file_to_base64(filepath)
        }
    }

    # Make a POST request to the Anki-Connect API with the request payload as the body
    url = "http://localhost:8765"
    request_json = json.dumps(request_payload)
    response = requests.post(url, data=request_json)

    # Parse the response JSON and get the URL of the uploaded file
    response_json = json.loads(response.text)
    err = response_json.get('error', None)
    if err:
        print(f'upload mp3 error: {err}')
    audio_url = response_json["result"]
    return audio_url


def add_to_anki(word, word_type):
    print(f'ADD_TO_ANKI: {word} ({word_type})')
    info = load_word_info(word)
    audio_url = anki_media_exist_for_word(word) or upload_mp3_for_card(word)
    card = build_anki_card(word, word_type, info, audio_url)
    # 单词类型 → 模型的映射原来写死在 add_anki_card 里，现在由调用方显式选择
    model_name = '基础' if word_type == '名词' else 'ShuffledCloze'
    add_result = add_anki_card(card, model_name)
    return add_result and True or False


def mark_as_added_to_anki(word):
    new_info_path = get_new_info_path_for_word(word)
    archived_path = get_archived_path_for_word(word)
    mp3_path = get_mp3_path_for_word(word)

    if not os.path.exists(new_info_path):
        return False

    if os.path.exists(archived_path):
        os.unlink(archived_path)

    if os.path.exists(mp3_path):
        os.unlink(mp3_path)

    archived_sub_dir = os.path.dirname(archived_path)
    if not os.path.isdir(archived_sub_dir):
        os.makedirs(archived_sub_dir)

    os.rename(new_info_path, archived_path)
    return True


def make_anki_cards_from_word_list(force, use_google_sound):
    wordlist = get_word_list()
    n = len(wordlist)
    finished = 0
    failed = []
    skipped = []
    for i, (word, word_type, word_lang) in enumerate(wordlist):
        print(f'{i+1} / {n}: {word}')
        if is_word_archieved(word):
            if not force:
                print(f'SKIP: already added to Anki: {word}')
                skipped.append(word)
                finished = finished + 1
                continue
            else:
                print(f'FORCE: re-fetch info and sound: {word}')

        fetch_and_save_info(word, word_type, force)
        fetch_and_save_sound(word, word_lang, use_google_sound)

        if already_have_info_for_word(word) and sound_exist_for_word(word):
            if add_to_anki(word, word_type):
                mark_as_added_to_anki(word)
                finished = finished + 1
                continue

        failed.append(word)
    print(f'FINISHED: {finished} / {n}')
    if failed:
        print(f'NO-ADDED: ', '\n'.join(failed))
    if skipped:
        print(f'SKIPPED: ', '\n'.join(skipped))


def fetch_info_for_cards(force):
    wordlist = get_word_list()
    n = len(wordlist)
    finished = 0
    failed = []
    skipped = []
    for i, (word, word_type, word_lang) in enumerate(wordlist):
        print(f'{i+1} / {n}: {word}')
        if not force and already_have_info_for_word(word):
            print(f'SKIP: already have info: {word}')
            skipped.append(word)
            finished = finished + 1
            continue

        fetch_and_save_info(word, word_type, force)

        if already_have_info_for_word(word):
            finished = finished + 1
        else:
            failed.append(word)
    print(f'FINISHED: {finished} / {n}')
    if failed:
        print(f'NO-INFO: ', '\n'.join(failed))
    if skipped:
        print(f'SKIPPED: ', '\n'.join(skipped))


def fetch_sounds_for_cards(force, use_google_sound):
    wordlist = get_word_list()
    n = len(wordlist)
    finished = 0
    failed = []
    skipped = []
    for i, (word, word_type, word_lang) in enumerate(wordlist):
        print(f'{i+1} / {n}: {word}')
        if not force and sound_exist_for_word(word):
            print(f'SKIP: already have sound: {word}')
            skipped.append(word)
            finished = finished + 1
            continue

        fetch_and_save_sound(word, word_lang, use_google_sound)

        if sound_exist_for_word(word):
            finished = finished + 1
        else:
            failed.append(word)
    print(f'FINISHED: {finished} / {n}')
    if failed:
        print(f'NO-SOUND: ', '\n'.join(failed))
    if skipped:
        print(f'SKIPPED: ', '\n'.join(skipped))


def fetch_and_store_sounds(force, use_google_sound):
    wordlist = get_word_list()
    n = len(wordlist)
    finished = 0
    failed = []
    skipped = []
    for i, (word, word_type, word_lang) in enumerate(wordlist):
        print(f'{i+1} / {n}: {word}')
        if not force and anki_media_exist_for_word(word):
            print(f'[SKIP] no need to fetch sound: {word}')
            skipped.append(word)
            finished = finished + 1
            continue

        download_mp3_for_word(word, word_lang, use_google_sound)
        if not mp3_exist_for_word(word):
            print(f'[FAIL] download mp3 failed: {word}')
            failed.append(word)
            continue

        audio_url = upload_mp3_for_card(word)
        if not audio_url:
            print(f'[FAIL] download mp3 failed: {word}')
            failed.append(word)
            continue

        print(f'[DONE] {audio_url}')
        finished = finished + 1
    print(f'FINISHED: {finished} / {n}')
    if failed:
        print(f'NO-SOUND: ', '\n'.join(failed))
    if skipped:
        print(f'SKIPPED: ', '\n'.join(skipped))


def refetch_sound_for_word(word, use_youdao):
    """重取指定单词的发音，并覆盖 Anki 媒体库中的旧文件

    为什么不用 -S 流程（fetch_and_store_sounds）改改参数实现：
    - -S 按 wordlist.yaml 全量处理，无法只针对单个单词
    - -S 固定上传为 _EAUTO_ 前缀名，而旧版脚本生成的卡片引用的是
      无前缀的 <word>.mp3，同名覆盖不了旧卡，还会留下孤儿文件
    所以这里先探测 Anki 里实际存在的文件名（即卡片正在引用的），
    以同名覆盖上传，卡片无需重建即可生效
    """
    word = word.strip()
    # 从 wordlist 查语言（含辨析组）；查不到按英语处理，gTTS 的 lang 参数需要它
    lang = 'en'
    for w, _, word_lang in get_word_list():
        if w == word:
            lang = word_lang
            break
    else:
        for word_list, word_lang in get_compare_groups():
            if word in word_list:
                lang = word_lang
                break

    # 探测 Anki 媒体库里已有的文件名（新旧两种命名都可能）
    basename = os.path.basename(get_mp3_path_for_word(word))
    candidates = ['_EAUTO_' + basename, basename]
    targets = [name for name in candidates if check_media_for_file(name)]
    if targets:
        print(f'Anki 中已有该单词的音频: {targets}，将以同名覆盖')
    else:
        # 没找到：可能是单词还没添加过，或卡片引用了其他文件名。
        # 仍按当前代码的命名规范上传，但明确提示用户核对
        targets = ['_EAUTO_' + basename]
        print(f'警告: Anki 媒体库中未找到 {word} 的音频，将上传为 {targets[0]}（请确认卡片是否引用此文件名）')

    # 重新下载发音；需要修复发音时多数是有道 TTS 念错，所以默认 gTTS
    download_mp3_for_word(word, lang, use_google_sound=not use_youdao)
    if not mp3_exist_for_word(word):
        print(f'[FAIL] 下载发音失败: {word}')
        return

    for name in targets:
        result = upload_mp3_for_card(word, filename=name)
        if result:
            print(f'[DONE] 已覆盖上传: {name}')
        else:
            print(f'[FAIL] 上传失败: {name}')

    # 本地文件只是上传的中转，传完即删，与 add 流程的 mark_as_added 行为一致
    mp3_path = get_mp3_path_for_word(word)
    if os.path.exists(mp3_path):
        os.unlink(mp3_path)
    print('完成。Anki 同步后其他设备生效')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-f', '--force', action='store_true', help='force to re-fetch info and sound')
    parser.add_argument('-s', '--sound-only', action='store_true', help='fetch sound only')
    parser.add_argument('-S', '--store-sound', action='store_true', help='fetch and store sound')
    parser.add_argument('-g', '--google-sound', action='store_true', help='fetch sound from google')
    parser.add_argument('-i', '--info-only', action='store_true', help='fetch info only')
    parser.add_argument('-c', '--compare', action='store_true', help='单词辨析模式')
    parser.add_argument('-r', '--refetch-sound', metavar='WORD',
                        help='重新获取指定单词的发音并覆盖 Anki 媒体（默认 gTTS，用于修复念错的发音）')
    parser.add_argument('--youdao', action='store_true',
                        help='配合 -r 使用有道发音（默认 gTTS）')
    args = parser.parse_args()

    force = args.force
    use_google_sound = args.google_sound

    # 检查是否有辨析组配置
    compare_groups = get_compare_groups()

    if args.compare:
        # 显式 -c：只跑辨析模式
        if not compare_groups:
            print('未找到辨析组配置（compare 节点）')
            return
        make_comparison_cards(force, use_google_sound)
    elif args.sound_only:
        fetch_sounds_for_cards(force, use_google_sound)
    elif args.store_sound:
        fetch_and_store_sounds(force, use_google_sound)
    elif args.refetch_sound:
        refetch_sound_for_word(args.refetch_sound, use_youdao=args.youdao)
    elif args.info_only:
        fetch_info_for_cards(force)
    else:
        # 默认模式串行处理所有待办：普通类型组（default/名词/词组…）
        # + 辨析组。不再互斥：原来「有 compare 节就只跑辨析」导致普通
        # 类型被静默跳过，用户得手动注释切换。防重靠各流程自身的机制：
        # 普通类型走 archived 归档，辨析组走已处理标记
        make_anki_cards_from_word_list(force, use_google_sound)
        if compare_groups:
            make_comparison_cards(force, use_google_sound)


if __name__ == '__main__':
    main()

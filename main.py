# coding: utf-8

import os
import sys
import requests
import json
from bs4 import BeautifulSoup
import base64
import time
import argparse
from gtts import gTTS
import yaml

SAMPLE_API_KEY = 'sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'

if os.path.exists('config.json'):
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
else:
    config = {
        "API_KEY": SAMPLE_API_KEY,
        "PROXIES": { "http": "http://localhost:8123", "https": "http://localhost:8123" }
    }
    with open('config.json', 'w', encoding='utf-8') as f:
        json.dump(config, f)

# Set your OpenAI API key and proxy settings
API_KEY = os.environ.get('DEEPSEEK_API_KEY', config.get('API_KEY', SAMPLE_API_KEY))
PROXIES = config['PROXIES']

if API_KEY == SAMPLE_API_KEY:
    print('API_KEY not found')
    sys.exit(1)

# API_URL = "https://api.openai.com/v1/chat/completions"
# API_URL = "https://api.ohmygpt.com/v1/chat/completions"
# API_URL = "https://aigptx.top/v1/chat/completions"
API_URL = "https://api.deepseek.com/chat/completions"
# LLM_MODEL = "gpt-3.5-turbo"
LLM_MODEL = "deepseek-chat"
TIMEOUT_SECONDS = 25
REQUIRED_TAGS = ('单词', '意思', '音标', '例句', '例句翻译')
ARCHIVED_DIR = 'archived'

# Function to post a query to ChatGPT and retrieve a response
def ask_gpt(prompt):
    # Set the parameters for the API request
    # model_engine = "davinci"  # Choose the GPT model engine to use
    max_tokens = 50  # Set the maximum number of tokens in the response
    temperature = 0.5  # Set the "creativity" of the response
    stop = "\n"  # Set the stop sequence for the response

    # Set the headers for the API request
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }

    # Set the data for the API request
    system_prompt = ''
    messages = [{"role": "system", "content": system_prompt}]

    what_i_ask_now = {}
    what_i_ask_now["role"] = "user"
    what_i_ask_now["content"] = prompt
    messages.append(what_i_ask_now)

    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 1.0,  # 1.0,
        "top_p": 1.0,  # 1.0,
        "n": 1,
        "stream": False,
        "presence_penalty": 0,
        "frequency_penalty": 0,
    }

    # Post the query to the API and retrieve the response
    response = requests.post(
        url=API_URL,
        headers=headers,
        # proxies=PROXIES,
        json=payload,
        stream=False,
        timeout=TIMEOUT_SECONDS
    )

    # Extract the response text and return it
    answer = json.loads(response.text)
    if 'error' in answer:
        print('!! ERROR !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!')
        if 'message' in answer['error']:
            print(answer['error']['message'])
        else:
            print(answer['error'])
    return answer['choices'][0]['message']['content']



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


def get_word_info_new(word):
    with open('prompt_2.txt', 'r', encoding='utf-8') as f:
        prompt = f.read() + f'{word}'
    for retry in range(5):
        try:
            gpt_answer = ask_gpt(prompt)
            break
        except Exception as e:
            print('<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')
            print(e)
            print('<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')
            print('retry:', retry)
            continue

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
    for retry in range(5):
        try:
            info = ask_gpt(prompt)
            break
        except Exception as e:
            print('<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')
            print(e)
            print('<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')
            print('retry:', retry)
            continue

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


def generate_comparison_sentences(word_list):
    """为一组单词生成例句（每词5句）

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

    words_str = '\n'.join([f'- {w}' for w in word_list])
    prompt = prompt_template.replace('{words}', words_str)

    for retry in range(5):
        try:
            gpt_answer = ask_gpt(prompt)
            break
        except Exception as e:
            print(f'generate_comparison_sentences error: {e}, retry: {retry}')
            continue

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


def analyze_sentence(sentence, target_word, other_words):
    """分析句子中词汇替换的可能性

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

    other_words_str = ', '.join(other_words)
    prompt = prompt_template \
        .replace('{sentence}', sentence) \
        .replace('{target_word}', target_word) \
        .replace('{other_words}', other_words_str)

    for retry in range(5):
        try:
            gpt_answer = ask_gpt(prompt)
            break
        except Exception as e:
            print(f'analyze_sentence error: {e}, retry: {retry}')
            continue

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
    """构建辨析卡片（使用基础模型）

    返回: dict, 包含 '正面' 和 '背面' 字段
    """
    # 将 ___ 替换为实际单词，生成 Front
    answer = analysis_result.get('answer', '')
    front = sentence.replace('___', '____')
    # 添加选项
    options_str = ' / '.join(all_words)
    front = f"{front} ( {options_str} )"

    # 构建 Back
    translation = analysis_result.get('translation', '')
    analysis = analysis_result.get('analysis', [])

    back_lines = [
        f"答案: <b>{answer}</b>",
        "",
        f"翻译：{translation}",
        "",
        f"[sound:{audio_url}]",
        ""
    ]

    for item in analysis:
        word = item.get('word', '')
        explanation = item.get('explanation', '')
        back_lines.append(f"- {word}：{explanation}")

    back = '\n'.join(back_lines)

    return {
        '正面': front,
        '背面': back,
        'Sort Field': answer,
        'QuestionHint': ''
    }


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
        for word in word_list:
            word_data = sentences_data.get(word, {})
            examples = word_data.get('examples', [])
            definition = word_data.get('definition', '')

            print(f'  处理单词 {word}，共 {len(examples)} 个例句')

            for ex_idx, example in enumerate(examples):
                if not example or '___' not in example:
                    print(f'    例句 {ex_idx + 1} 格式不正确，跳过')
                    continue

                # 分析句子
                other_words = [w for w in word_list if w != word]
                analysis = analyze_sentence(example, word, other_words)

                if not analysis:
                    print(f'    例句 {ex_idx + 1} 分析失败，跳过')
                    continue

                # 上传音频
                audio_url = anki_media_exist_for_word(word) or upload_mp3_for_card(word)

                # 构建卡片
                card = build_comparison_card(example, analysis, word_list, audio_url)

                # 添加到 Anki（使用基础模型）
                result = add_anki_card(card, '名词')  # '名词' 对应基础模型
                if result:
                    total_cards += 1
                    print(f'    例句 {ex_idx + 1} → 卡片已添加')
                else:
                    print(f'    例句 {ex_idx + 1} 添加失败')

    print(f'\n完成！共添加 {total_cards} 张辨析卡片')


def get_new_info_path_for_word(word):
    return os.path.join('new_info', word)


def get_archived_path_for_word(word):
    return os.path.join(ARCHIVED_DIR, time.strftime("%Y-%m"), word)


def is_word_archieved(word):
    for name in os.listdir(ARCHIVED_DIR):
        path = os.path.join(ARCHIVED_DIR, name)
        if not os.path.isdir(path):
            continue
        word_path = os.path.join(path, word)
        if not os.path.isfile(word_path):
            continue
        return True
    return False


def get_mp3_path_for_word(word):
    return os.path.join('sound', f"{word}.mp3")


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
    assert 'HTTP_PROXY' not in os.environ
    assert 'HTTPS_PROXY' not in os.environ
    os.environ['HTTP_PROXY'] = PROXIES['http']
    os.environ['HTTPS_PROXY'] = PROXIES['https']
    gTTS(text=word, lang=lang, slow=False).save(filepath)
    del os.environ['HTTP_PROXY']
    del os.environ['HTTPS_PROXY']


def fetch_and_save_info(word, word_type, force):
    if not force and already_have_info_for_word(word):
        return
    print(f'fetching info: {word}')
    if word_type == '名词':
        info = get_word_info_for_noun(word)
    else:
        info = get_word_info_new(word)
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


def add_anki_card(note_fields, word_type):
    # Set the URL for the Anki-Connect API
    url = "http://localhost:8765"

    # Set the action to add a new note
    action = "addNote"

    # Set the deck name to add the note to
    deck_name = "English::Arnan's English Sentences"

    # Set the model name to use for the new note
    if word_type == "名词":
        model_name = "基础"
    else:
        model_name = "ShuffledCloze"

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


def upload_mp3_for_card(word):
    # Construct the request payload as a Python dictionary
    filepath = get_mp3_path_for_word(word)
    request_payload = {
        "action": "storeMediaFile",
        "version": 6,
        "params": {
            "filename": '_EAUTO_' + os.path.basename(filepath),
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
    add_result = add_anki_card(card, word_type)
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-f', '--force', action='store_true', help='force to re-fetch info and sound')
    parser.add_argument('-s', '--sound-only', action='store_true', help='fetch sound only')
    parser.add_argument('-S', '--store-sound', action='store_true', help='fetch and store sound')
    parser.add_argument('-g', '--google-sound', action='store_true', help='fetch sound from google')
    parser.add_argument('-i', '--info-only', action='store_true', help='fetch info only')
    parser.add_argument('-c', '--compare', action='store_true', help='单词辨析模式')
    args = parser.parse_args()

    force = args.force
    use_google_sound = args.google_sound

    # 检查是否有辨析组配置
    compare_groups = get_compare_groups()

    if args.compare or (compare_groups and not args.sound_only and not args.store_sound and not args.info_only):
        # 辨析模式
        if not compare_groups:
            print('未找到辨析组配置（compare 节点）')
            return
        make_comparison_cards(force, use_google_sound)
    elif args.sound_only:
        fetch_sounds_for_cards(force, use_google_sound)
    elif args.store_sound:
        fetch_and_store_sounds(force, use_google_sound)
    elif args.info_only:
        fetch_info_for_cards(force)
    else:
        make_anki_cards_from_word_list(force, use_google_sound)


if __name__ == '__main__':
    main()

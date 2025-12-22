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
    prompt = '''请扮演专业的面向中高级学习者的 ESL teacher，对给出的单词进行讲解，给出其音标、各种常用的意思、以及5个能从多方面展示其用法的例句。

## 输出格式要求
Return the answer as a JSON object.

## 示例1
```json
{
    "word": "savvy",
    "pronunciation": "/ˈsævi/",
    "definition": [
        "n. 实际知识，见识，悟性。指在特定领域（尤指商业或政治）的实用理解和精明判断力。",
        "adj. 精明的，有见识的，懂行的。形容对某事有深刻理解和实际知识的人。",
        "v. 理解，懂（非正式用法，尤其用于疑问句或否定句）。",
        "注意：作为名词时，常与特定领域连用，如 'political {{c1::savvy}}'（政治悟性）。作为形容词时，常置于名词后构成复合形容词，如 'tech-{{c1::savvy}}'（精通科技的）。与 'knowledgeable'（知识渊博的）相比，'{{c1::savvy}}' 更强调实用的、街头智慧式的精明。"
    ],
    "example1": "She has impressive business {{c1::savvy}}, which is why her startup has been so successful.",
    "example2": "To succeed in today's market, you need to be financially {{c1::savvy}} and aware of the latest trends.",
    "example3": "I don't think he {{c1::savvies}} the full implications of this deal; we need to explain it more clearly.",
    "example4": "The campaign was run by a team of politically {{c1::savvy}} advisors who knew how to connect with voters.",
    "example5": "\"{{c1::Savvy}}\" is a word we use to describe practical knowledge, understanding, or shrewdness, especially in a particular field like business or technology."
}
```

## 示例2
```json
{
  "word": "dough",
  "pronunciation": "/doʊ/",
  "definition": [
    "n. 生面团",
    "指用于制作面包、糕点等的生面团，通常由面粉、水和其他成分混合而成",
    "在俚语中表示金钱，尤指现金",
    "指任何具有类似面团稠度的柔软、可塑物质"
  ],
  "example1": "She kneaded the {{c1::dough}} for ten minutes until it was smooth and elastic.",
  "example2": "I need to earn some more {{c1::dough}} before I can afford that vacation.",
  "example3": "After adding the yeast, let the {{c1::dough}} rise in a warm place for an hour.",
  "example4": "He's rolling in {{c1::dough}} since he started his new business.",
  "example5": "The children enjoyed playing with the salt {{c1::dough}} they made in art class."
}
```

## 注意
- 确保在每个例句中，使用 {{c1::word}} 标记来突出单词的位置。与 Anki Cloze 要求格式兼容。
- 请确保给出的单词包含在了每个例句中，且使用了 {{c1::word}} 标记来突出单词的位置，符合 Anki Cloze 要求格式。
- 若单词有多词性，请在 definition 中分条列出。
- 尽量指出单词与常见同义词相比在使用上需要注意的细微差别。
- definition 使用自然的中文释义。
- definition 中包含了 word 的地方，也改为使用 {{c1::word}} 的形式。
- 给出的例句尽量覆盖不同语境、不同语法位置。
- 例句可以包含一句以 "{{c1::word}} is a word ..." 的形式包含单词英文释义的句子。
- 尽量选取使用单数、现在时态的例句。
- Do not write any commentary outside the JSON.

## 输入
要处理的单词是: ''' + f'{word}'
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
    words = []
    with open('wordlist.txt', 'r', encoding='utf-8') as f:
        for line in f.readlines():
            if line.startswith('#'):
                continue
            c = line.strip()
            if ':' in c:
                word, word_type = c.split(':', 1)
            else:
                word, word_type = c, None
            words.append((word, word_type))
    return words


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


def download_mp3_for_word(word):
    print(f'fetching MP3: {word}')
    filepath = get_mp3_path_for_word(word)

    # 尝试从有道获取
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
    gTTS(text=word, lang='en', slow=False).save(filepath)
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


def already_have_sound_for_word(word):
    filepath = get_mp3_path_for_word(word)
    if os.path.exists(filepath):
        return True

    if check_media_for_mp3(word):
        return True

    return False


def fetch_and_save_sound(word):
    if already_have_sound_for_word(word):
        print('AUDIO ALREADY EXIST:', word)
        return
    download_mp3_for_word(word)


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


def check_media_for_mp3(word):
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
    audio_url = check_media_for_mp3(word) or upload_mp3_for_card(word)
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


def make_anki_cards_from_word_list(force):
    wordlist = get_word_list()
    n = len(wordlist)
    finished = 0
    failed = []
    skipped = []
    for i, (word, word_type) in enumerate(wordlist):
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
        fetch_and_save_sound(word)

        if already_have_info_for_word(word) and already_have_sound_for_word(word):
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
    for i, (word, word_type) in enumerate(wordlist):
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


def fetch_sounds_for_cards(force):
    wordlist = get_word_list()
    n = len(wordlist)
    finished = 0
    failed = []
    skipped = []
    for i, (word, word_type) in enumerate(wordlist):
        print(f'{i+1} / {n}: {word}')
        if not force and already_have_sound_for_word(word):
            print(f'SKIP: already have sound: {word}')
            skipped.append(word)
            finished = finished + 1
            continue

        fetch_and_save_sound(word)

        if already_have_sound_for_word(word):
            finished = finished + 1
        else:
            failed.append(word)
    print(f'FINISHED: {finished} / {n}')
    if failed:
        print(f'NO-SOUND: ', '\n'.join(failed))
    if skipped:
        print(f'SKIPPED: ', '\n'.join(skipped))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-f', '--force', action='store_true', help='force to re-fetch info and sound')
    parser.add_argument('-s', '--sound-only', action='store_true', help='fetch sound only')
    parser.add_argument('-i', '--info-only', action='store_true', help='fetch info only')
    args = parser.parse_args()

    force = args.force

    if args.sound_only:
        fetch_sounds_for_cards(force)
    elif args.info_only:
        fetch_info_for_cards(force)
    else:
        make_anki_cards_from_word_list(force)


if __name__ == '__main__':
    main()

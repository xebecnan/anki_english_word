# coding: utf-8

import os
import sys
import requests
import json
from bs4 import BeautifulSoup
import base64
import time

SAMPLE_OPEN_API_KEY = 'sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'

if os.path.exists('config.json'):
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
else:
    config = {
        "OPENAI_API_KEY": SAMPLE_OPEN_API_KEY,
        "PROXIES": { "http": "http://localhost:8123", "https": "http://localhost:8123" }
    }
    with open('config.json', 'w', encoding='utf-8') as f:
        json.dump(config, f)

# Set your OpenAI API key and proxy settings
OPENAI_API_KEY = config['OPENAI_API_KEY']
PROXIES = config['PROXIES']

if OPENAI_API_KEY == SAMPLE_OPEN_API_KEY:
    print('please edit the config.json')
    sys.exit(1)

API_URL = "https://api.openai.com/v1/chat/completions"
LLM_MODEL = "gpt-3.5-turbo"
TIMEOUT_SECONDS = 25
REQUIRED_TAGS = ('单词', '意思', '音标', '例句', '例句翻译')
ARCHIVED_DIR = 'archived'

# Function to post a query to ChatGPT and retrieve a response
def ask_gpt(prompt):
    # Set the parameters for the API request
    model_engine = "davinci"  # Choose the GPT model engine to use
    max_tokens = 50  # Set the maximum number of tokens in the response
    temperature = 0.5  # Set the "creativity" of the response
    stop = "\n"  # Set the stop sequence for the response

    # Set the headers for the API request
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}",
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
        proxies=PROXIES,
        json=payload,
        stream=False,
        timeout=TIMEOUT_SECONDS
    )

    # Extract the response text and return it
    answer = json.loads(response.text)
    return answer['choices'][0]['message']['content']



def load_info(info):
    parsed = {}
    for line in info.splitlines():
        tag, body = line.strip().split('：', 1)
        parsed[tag] = body
    return parsed


def check_info(info):
    parsed = load_info(info)
    for tag in REQUIRED_TAGS:
        if tag not in parsed:
            print(f'PARSE ERROR! tag:{tag} word:{word}')
            return False
    return True

def get_word_info(word):
    prompt = f'''单词：{word}
意思：____
音标：/____/
例句：____
例句翻译：____'''
    info = ask_gpt(prompt)
    return check_info(info) and info or None


def get_word_list():
    words = []
    with open('wordlist.txt', 'r', encoding='utf-8') as f:
        for line in f.readlines():
            if line.startswith('#'):
                continue
            words.append(line.strip())
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
    response = requests.get(url)

    if response.ok:
        return response.content
    else:
        return None


def download_mp3_for_word(word):
    print(f'fetching MP3: {word}')
    mp3_content = get_pronunciation_mp3(word)
    if mp3_content:
        filepath = get_mp3_path_for_word(word)
        with open(filepath, "wb") as f:
            f.write(mp3_content)
    else:
        print(f'DOWNLOADED MP3 FAILED {word}')


def fetch_and_save_info(word):
    if already_have_info_for_word(word):
        return
    print(f'fetching info: {word}')
    info = get_word_info(word)
    if info:
        save_word_info(word, info)


def already_have_sound_for_word(word):
    filepath = get_mp3_path_for_word(word)
    return os.path.exists(filepath)


def fetch_and_save_sound(word):
    if already_have_sound_for_word(word):
        return
    download_mp3_for_word(word)


def add_anki_card(note_fields):
    # Set the URL for the Anki-Connect API
    url = "http://localhost:8765"

    # Set the action to add a new note
    action = "addNote"

    # Set the deck name to add the note to
    deck_name = "English::Arnan's English Sentences"

    # Set the model name to use for the new note
    model_name = "基础"

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

    # Make a POST request to the Anki-Connect API with the request payload as the body
    response = requests.post(url, data=request_json)

    # print("response.text:", response.text)
    # Parse the response JSON and return the result
    response_json = json.loads(response.text)
    err = response_json.get('error', None)
    if err:
        print(f'add anki card error: {err}')
    return response_json["result"]


def build_anki_card(info, audio_url):
    parsed = load_info(info)
    return {
        '正面': parsed['例句'],
        '背面': parsed['例句翻译'],
        'Detail': f'{parsed["单词"]} {parsed["意思"]} {parsed["音标"]}',
        'Audio': f'[sound:{audio_url}]',
        'Sort Field': parsed["单词"],
        'QuestionHint': ''
    }


def file_to_base64(file_path):
    with open(file_path, "rb") as f:
        data = f.read()
    return base64.b64encode(data).decode("utf-8")


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


def add_to_anki(word):
    print(f'ADD_TO_ANKI: {word}')
    info = load_word_info(word)
    audio_url = upload_mp3_for_card(word)
    card = build_anki_card(info, audio_url)
    add_result = add_anki_card(card)
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

    # TODO: 避免 archived 目录中同时存在太多文件
    # 或许可以编写一个 archive_util 模块，检测目录文件太多时自动拆分到子目录中，提供 add 和 exists 接口
    os.rename(new_info_path, archived_path)
    return True


words = get_word_list()
n = len(words)
for i, word in enumerate(words):
    print(f'{i+1} / {n}: {word}')
    if is_word_archieved(word):
        print(f'SKIP: already added to Anki: {word}')
        continue

    fetch_and_save_info(word)
    fetch_and_save_sound(word)

    if already_have_info_for_word(word) and already_have_sound_for_word(word):
        if add_to_anki(word):
            mark_as_added_to_anki(word)

# print(ask_gpt('我想用建立两个文件夹，其中一个用来存放刚获取到的信息，对这些信息进行处理之后，再将其移到另一个用于存放已归档信息的文件夹。请为这两个文件夹用英文单词命名'))


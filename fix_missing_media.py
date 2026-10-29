# coding: utf-8
"""修复 Anki 媒体库中缺失的 _EAUTO_<word>.mp3

背景：卡片通过 AnkiConnect 上传音频时以 _EAUTO_ 前缀命名，但媒体库里可能
只剩旧版脚本生成的无前缀 <word>.mp3（历史命名/同步等造成），导致 Anki
「检查媒体」报缺失。本脚本把缺的文件补齐，卡片无需重建。

对每个单词的处理顺序（幂等，可重复运行）：
1. 媒体库已有 _EAUTO_<word>.mp3 → 跳过
2. 媒体库有无前缀 <word>.mp3 → 把现有音频按 _EAUTO_ 名复制一份
   （复用卡片原本的发音，不重新下载）
3. 两个名字都没有 → 有道下载（失败则 gTTS 兜底）后上传，本地中转文件即删

用法：
    python fix_missing_media.py                 # 修复 MISSING_WORDS 里的全部单词
    python fix_missing_media.py moss review     # 只修复指定单词

修复后需在 Anki 里重新「检查媒体」，并同步让其他设备拿到文件。
"""
import argparse
import base64
import os
import requests

import main  # 复用下载逻辑（有道 → gTTS 兜底、代理配置）与路径函数

ANKI_URL = 'http://localhost:8765'

# 2026-10 检查媒体报告的缺失清单
MISSING_WORDS = [
    'aptitude', 'talent', 'arable', 'effort', 'fertile', 'literary',
    'moss', 'nip', 'plight', 'review', 'revise', 'scum', 'speckle', 'startle',
]


def anki(action, **params):
    payload = {'action': action, 'version': 6}
    if params:
        payload['params'] = params
    data = requests.post(ANKI_URL, json=payload, timeout=30).json()
    if data.get('error'):
        raise RuntimeError(f'{action} error: {data["error"]}')
    return data['result']


def media_file_bytes(filename):
    """读取媒体库文件内容；不存在返回 None"""
    data = anki('retrieveMediaFile', filename=filename)
    if data in (False, None, ''):
        return None
    return base64.b64decode(data)


def upload_bytes(filename, content):
    result = anki('storeMediaFile', filename=filename, data=base64.b64encode(content).decode())
    if not result:
        raise RuntimeError(f'storeMediaFile {filename} 失败')
    return result


def fix_word(word):
    """补齐单个单词的 _EAUTO_ 音频，返回处理方式字符串"""
    target = f'_EAUTO_{word}.mp3'

    if media_file_bytes(target) is not None:
        return 'already-ok'

    # 先看媒体库里有没有旧命名的同名文件，有就直接复制，保证发音和卡片一致
    legacy = media_file_bytes(f'{word}.mp3')
    if legacy is not None:
        upload_bytes(target, legacy)
        return 'copied'

    # 彻底缺失：走项目的下载流程（有道优先，gTTS 兜底），落地到 sound/ 再上传
    filepath = main.get_mp3_path_for_word(word)
    main.download_mp3_for_word(word, 'en', use_google_sound=False)
    with open(filepath, 'rb') as f:
        content = f.read()
    upload_bytes(target, content)
    # 本地文件只是上传的中转，传完即删，与 add 流程的 mark_as_added 行为一致
    os.unlink(filepath)
    return 'downloaded'


def main_fix(words):
    counts = {}
    for word in words:
        try:
            how = fix_word(word)
        except Exception as e:
            how = f'FAILED ({e})'
        counts[how] = counts.get(how, 0) + 1
        print(f'[{how:12s}] {word}')

    # 逐词最终校验，确保没有漏网的
    print('\n--- 校验 ---')
    bad = []
    for word in words:
        if media_file_bytes(f'_EAUTO_{word}.mp3') is None:
            bad.append(word)
            print(f'[MISS] _EAUTO_{word}.mp3 仍然缺失！')
    print(f'完成: {len(words) - len(bad)}/{len(words)} 就绪，处理方式统计: {counts}')
    if bad:
        print(f'仍缺失: {bad}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='修复 Anki 媒体库缺失的 _EAUTO_ 音频')
    parser.add_argument('words', nargs='*', help='要修复的单词；留空则处理全部 MISSING_WORDS')
    args = parser.parse_args()
    main_fix(args.words or MISSING_WORDS)

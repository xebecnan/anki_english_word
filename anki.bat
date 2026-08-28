@echo off
rem 启动 anki-english-word 的便捷入口
rem 为什么用 .bat 而不是直接双击 exe：让命令行参数（如 %*）能原样透传，同时避免出现错误时窗口一闪而过

".venv\Scripts\anki-english-word.exe" %*

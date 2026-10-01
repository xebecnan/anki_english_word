@echo off
rem 启动 anki-english-word 的便捷入口
rem 为什么用 .bat 而不是直接双击 exe：让命令行参数（如 %*）能原样透传，同时避免出现错误时窗口一闪而过
rem 本文件必须保存为 ANSI(GBK) 编码，不能用 UTF-8：中文 Windows 的 cmd 按 GBK
rem 解析批处理文件，UTF-8 的中文注释会被误读成乱码并被当作命令执行
".venv\Scripts\anki-english-word.exe" %*
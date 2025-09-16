from flask import Flask, render_template_string, request, jsonify, send_file, session
import yt_dlp
import os
import datetime
import uuid
import threading
import time
import logging
from pathlib import Path
import re
import json
import shutil
import requests
import mimetypes

# Helper: try to locate ffmpeg in common places and project folder
def find_ffmpeg():
    # 1) Check environment PATH
    for name in ("ffmpeg.exe", "ffmpeg"):
        path = shutil.which(name)
        if path:
            return path

    # 2) Check local extraction folder relative to this script file
    script_dir = Path(__file__).resolve().parent
    local_paths = [
        script_dir / 'ffmpeg-7.1.1-essentials_build' / 'bin' / 'ffmpeg.exe',
        script_dir / 'ffmpeg-7.1.1-essentials_build' / 'bin' / 'ffmpeg'
    ]
    for p in local_paths:
        if p.exists():
            return str(p)

    return None

# Инициализация Flask приложения
app = Flask(__name__)
app.secret_key = 'your-secret-key-' + str(uuid.uuid4())

# Настройка путей
DOWNLOADS_DIR = Path('downloads')
DOWNLOADS_DIR.mkdir(exist_ok=True)
LOG_FILE = DOWNLOADS_DIR / 'log.txt'

# Toggle: when True, if a file already exists we treat it as "already downloaded" and skip.
# When False, existing files will be removed and the downloader will re-download (useful for forcing fresh files).
CHECK_DOWNLOADED = True

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Хранилище прогресса загрузок
download_progress = {}
# Флаги отмены загрузок
download_cancelled = {}

# Простая мапа код->полное русское название + флаг (дополняйте по необходимости)
LANG_LABELS = {
    'en': 'Английский 🇬🇧',
    'ru': 'Русский 🇷🇺',
    'es': 'Испанский 🇪🇸',
    'fr': 'Французский 🇫🇷',
    'de': 'Немецкий 🇩🇪',
    'pt': 'Португальский 🇵🇹',
    'zh': 'Китайский 🇨🇳',
    'zh-cn': 'Китайский (упрощ.) 🇨🇳',
    'zh-tw': 'Китайский (традиц.) 🇹🇼',
    'ja': 'Японский 🇯🇵',
    'ko': 'Корейский 🇰🇷',
    'ar': 'Арабский 🇸🇦',
    'hi': 'Хинди 🇮🇳',
    'vi': 'Вьетнамский 🇻🇳',
    'id': 'Индонезийский 🇮🇩',
    'it': 'Итальянский 🇮🇹',
    'nl': 'Нидерландский 🇳🇱',
    'sv': 'Шведский 🇸🇪',
    'no': 'Норвежский 🇳🇴',
    'da': 'Датский 🇩🇰',
    'fi': 'Финский 🇫🇮',
    'he': 'Иврит 🇮🇱',
    'el': 'Греческий 🇬🇷',
    'cs': 'Чешский 🇨🇿',
    'pl': 'Польский 🇵🇱',
    'uk': 'Украинский 🇺🇦',
    'ro': 'Румынский 🇷🇴',
    'hu': 'Венгерский 🇭🇺',
    'bg': 'Болгарский 🇧🇬',
    'sr': 'Сербский 🇷🇸',
    'hr': 'Хорватский 🇭🇷',
    'sk': 'Словацкий 🇸🇰',
    'sl': 'Словенский 🇸🇮',
    'lv': 'Латышский 🇱🇻',
    'lt': 'Литовский 🇱🇹',
    'et': 'Эстонский 🇪🇪',
    'ms': 'Малайский 🇲🇾',
    'bn': 'Бенгальский 🇧🇩',
    'ur': 'Урду 🇵🇰',
    'gu': 'Гуджарати 🇮🇳',
    'kn': 'Каннада 🇮🇳',
    'ml': 'Малаялам 🇮🇳',
    'te': 'Телугу 🇮🇳',
    'ta': 'Тамильский 🇮🇳',
    'mr': 'Маратхи 🇮🇳',
    'pa': 'Пенджаби 🇮🇳',
    'ne': 'Непальский 🇳🇵',
    'si': 'Сингальский 🇱🇰',
    'th': 'Тайский 🇹🇭',
    'km': 'Кхмерский 🇰🇭',
    'lo': 'Лаосский 🇱🇦',
    'my': 'Бирманский 🇲🇲',
    'tr': 'Турецкий 🇹🇷',
    'fa': 'Персидский 🇮🇷',
    'pt-br': 'Португальский (Браз.) 🇧🇷',
    'es-419': 'Испанский (Лат.) 🇪🇸'
}

def get_lang_label(code: str):
    if not code:
        return '(не указано)'
    code = code.lower()
    # normalize some legacy/region variants
    if code == 'in':
        code = 'id'
    if code == 'iw':
        code = 'he'
    # try full code then base
    if code in LANG_LABELS:
        return LANG_LABELS[code]
    base = code.split('-')[0]
    if base in LANG_LABELS:
        return LANG_LABELS[base]
    # fallback: attempt to prettify code (e.g., 'pt' -> 'pt')
    return f"{base} ({code})"

# HTML шаблон с современным интерфейсом
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🎬 Универсальный Загрузчик Видео</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        
        .container {
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 40px;
            max-width: 600px;
            width: 100%;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            animation: fadeIn 0.5s ease;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        h1 {
            text-align: center;
            color: #333;
            margin-bottom: 10px;
            font-size: 2em;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .subtitle {
            text-align: center;
            color: #666;
            margin-bottom: 30px;
            font-size: 0.9em;
        }
        
        .supported-sites {
            display: flex;
            justify-content: center;
            gap: 15px;
            margin-bottom: 30px;
            flex-wrap: wrap;
        }
        
        .site-badge {
            padding: 5px 12px;
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            color: white;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 500;
        }
        
        .input-group {
            margin-bottom: 20px;
        }
        
        label {
            display: block;
            margin-bottom: 8px;
            color: #555;
            font-weight: 500;
        }
        
        input[type="text"] {
            width: 100%;
            padding: 15px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 16px;
            transition: all 0.3s;
        }
        
        input[type="text"]:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        
        .format-selector {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        
        .format-btn {
            flex: 1;
            padding: 12px;
            border: 2px solid #e0e0e0;
            background: white;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.3s;
            font-size: 16px;
            font-weight: 500;
        }
        
        .format-btn:hover {
            border-color: #667eea;
            background: rgba(102, 126, 234, 0.05);
        }
        
        .format-btn.active {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-color: transparent;
        }

        /* Photo button styled as disabled/gray to indicate it's not ready */
        .format-btn[data-format="photo"] {
            background: #f0f0f0;
            color: #6b6b6b;
            border-color: #e0e0e0;
        }
        .format-btn[data-format="photo"].active {
            /* If it ever becomes active, keep a muted style */
            background: linear-gradient(135deg,#bdbdbd,#9e9e9e);
            color: white;
            border-color: transparent;
        }
        
        .quality-selector {
            margin-bottom: 20px;
            display: none;
        }
        
        select {
            width: 100%;
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 16px;
            background: white;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        select:focus {
            outline: none;
            border-color: #667eea;
        }
        
        .download-btn {
            width: 100%;
            padding: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 18px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
            position: relative;
            overflow: hidden;
        }
        
        .download-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(102, 126, 234, 0.3);
        }
        
        .download-btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }
        
        .progress-container {
            margin-top: 30px;
            display: none;
        }
        
        .progress-bar {
            width: 100%;
            height: 30px;
            background: #e0e0e0;
            border-radius: 15px;
            overflow: hidden;
            position: relative;
        }
        
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            width: 0%;
            transition: width 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: 600;
            font-size: 14px;
            position: relative;
            overflow: hidden;
        }
        
        .progress-fill::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent);
            animation: shimmer 2s infinite;
        }
        
        @keyframes shimmer {
            to { left: 100%; }
        }
        
        .status-message {
            margin-top: 15px;
            padding: 10px;
            border-radius: 10px;
            text-align: center;
            display: none;
        }
        
        .status-message.success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        
        .status-message.error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        
        .media-player {
            margin-top: 20px;
            display: none;
            background-color: #000;
            border-radius: 15px;
            overflow: hidden;
            position: relative;
        }

        .media-player video {
            display: block;
            width: 100%;
            height: auto;
            max-height: 80vh;
        }

        .media-player.is-vertical video {
            width: auto;
            height: 100%;
            max-width: 100%;
            margin: 0 auto;
        }

        .media-player-container {
            width: 100%;
            height: 80vh;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
        }

        /* Toast / popup notifications */
        .toast-container {
            position: fixed;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 9999;
            display: flex;
            flex-direction: column;
            gap: 10px;
            align-items: center;
            pointer-events: none; /* allow clicks through when toasts are present */
        }

        .toast {
            width: auto;
            max-width: 960px; /* centered banner max width */
            padding: 16px 22px;
            border-radius: 12px; /* rounded corners */
            color: white;
            background: linear-gradient(90deg, #43cea2, #185a9d);
            box-shadow: 0 10px 30px rgba(0,0,0,0.18);
            font-weight: 700;
            font-size: 18px;
            opacity: 0.98;
            transform: translateY(-8px);
            animation: slideIn 240ms ease-out;
            pointer-events: auto;
            text-align: center; /* center the text */
        }

        .toast .progress-line {
            position: absolute;
            left: 0;
            bottom: 0;
            height: 6px; /* progress line height */
            background: rgba(255,255,255,0.95);
            width: 100%;
            border-bottom-left-radius: 12px;
            border-bottom-right-radius: 12px;
            transform-origin: left center;
        }

        @keyframes slideIn {
            from { opacity: 0; transform: translateY(-12px); }
            to { opacity: 0.95; transform: translateY(0); }
        }

        @keyframes fadeOut {
            from { opacity: 0.95; transform: translateY(0); }
            to { opacity: 0; transform: translateY(-8px); }
        }

        /* Centered small development popup */
        .dev-popup {
            position: fixed;
            left: 50%;
            top: 24%;
            transform: translateX(-50%);
            z-index: 10001;
            background: rgba(245,245,245,0.98);
            color: #7d7d7d; /* gray text */
            border: 1px solid #e6e6e6;
            padding: 12px 18px;
            border-radius: 10px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.08);
            font-weight: 700;
            pointer-events: none;
            opacity: 0;
            transition: opacity 180ms ease, transform 180ms ease;
        }
        .dev-popup.show {
            opacity: 1;
            transform: translateX(-50%) translateY(0);
        }

        .fade-out {
            animation: fadeOut 260ms ease-in forwards;
        }
        
        video, audio {
            max-width: 100%;
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        }
        
        .download-file-btn {
            margin-top: 15px;
            width: 100%;
            padding: 12px;
            background: linear-gradient(135deg, #56ab2f 0%, #a8e063 100%);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .download-file-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(86, 171, 47, 0.3);
        }
        
        @media (max-width: 600px) {
            .container {
                padding: 25px;
            }
            
            h1 {
                font-size: 1.5em;
            }
            
            .format-selector {
                flex-direction: column;
            }
        }
        
        .spinner {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid rgba(255,255,255,.3);
            border-radius: 50%;
            border-top-color: white;
            animation: spin 1s ease-in-out infinite;
            margin-left: 10px;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        /* Pretty toggle styles */
        .hidden-toggle { display: none; }
        .pretty-toggle {
            display: inline-flex;
            align-items: center;
            gap: 12px;
            cursor: pointer;
            user-select: none;
        }
        .pretty-toggle .toggle-track {
            width: 28px;
            height: 14px;
            background: linear-gradient(90deg, #e6e6e6, #d4d4d4);
            border-radius: 12px;
            padding: 2px;
            box-shadow: inset 0 1px 3px rgba(0,0,0,0.12), 0 3px 8px rgba(0,0,0,0.12);
            display: inline-flex;
            align-items: center;
            transition: background 180ms ease, box-shadow 180ms ease;
        }
        .pretty-toggle .toggle-thumb {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: linear-gradient(180deg,#ffffff, #e6f7ef);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: #0a6b3a;
            font-weight: 800;
            transform: translateX(0);
            transition: transform 200ms cubic-bezier(.22,1,.36,1), background 180ms ease;
            box-shadow: 0 4px 8px rgba(10,107,58,0.16);
            font-size: 9px;
        }
        .pretty-toggle .toggle-label { font-weight:600; color:#333; }
        /* when checkbox checked: move thumb and highlight track */
        input.hidden-toggle:checked + label.pretty-toggle .toggle-track {
            background: linear-gradient(90deg,#56c28a,#32916b);
            box-shadow: inset 0 2px 6px rgba(0,0,0,0.06), 0 10px 30px rgba(50,145,107,0.18);
        }
        input.hidden-toggle:checked + label.pretty-toggle .toggle-thumb {
            transform: translateX(14px);
            background: linear-gradient(180deg,#ffffff,#d6fff0);
            color: #0b5136;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎬 Универсальный Загрузчик Видео</h1>
        <p class="subtitle">Скачивайте видео и аудио с любимых платформ ✨</p>
        
        <div class="supported-sites">
            <span class="site-badge">YouTube</span>
            <span class="site-badge">TikTok</span>
            <span class="site-badge">Instagram</span>
            <span class="site-badge">Facebook</span>
            <span class="site-badge">И другие...</span>
        </div>
        
        <div class="input-group">
            <label for="url">🔗 Вставьте ссылку на видео:</label>
            <div style="display:flex; gap:8px; align-items:center;">
                <input type="text" id="url" placeholder="https://www.youtube.com/watch?v=..." autocomplete="off" style="flex:1;">
            </div>
        </div>

        <!-- audio language selection removed: single automatic audio behavior -->
        
        <div class="format-selector">
            <button class="format-btn active" data-format="video" onclick="selectFormat('video')">
                🎥 Видео
            </button>
            <button class="format-btn" data-format="audio" onclick="selectFormat('audio')">
                🎵 Аудио
            </button>
            <button class="format-btn" data-format="photo" onclick="selectFormat('photo')">
                🖼️ Фото
            </button>
        </div>
        <div style="margin-top:12px; display:flex; align-items:center; gap:8px;">
            <input type="checkbox" id="checkDownloaded" class="hidden-toggle" />
            <label for="checkDownloaded" class="pretty-toggle">
                <span class="toggle-track">
                    <span class="toggle-thumb">✓</span>
                </span>
                <span class="toggle-label">⚡ Быстрое скачивание</span>
            </label>
        </div>
        
        <div class="quality-selector" id="qualitySelector">
            <label for="quality">📺 Выберите качество:</label>
            <select id="quality">
                <option value="1080">1080p — Очень хорошее 📺</option>
                <option value="720" selected>720p — Хорошее 🎥</option>
                <option value="480">480p — Среднее 📼</option>
                <option value="360">360p — Плохое 📱</option>
            </select>
        </div>
        
        <button class="download-btn" onclick="startDownload()">
            ⬇️ Скачать
        </button>
        <button class="download-btn" id="cancelBtn" style="margin-top:10px; background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%); display:none;" onclick="cancelDownload()">
            ✖️ Отменить
        </button>
        
        <div class="progress-container" id="progressContainer">
            <div class="progress-bar">
                <div class="progress-fill" id="progressFill">0%</div>
            </div>
        </div>
        
        <div class="status-message" id="statusMessage"></div>
    <div class="toast-container" id="toastContainer"></div>
        
        <div class="media-player" id="mediaPlayer"></div>
    </div>
    
    <script>
        let selectedFormat = 'video';
        let downloadId = null;
        let progressInterval = null;
        
        function selectFormat(format) {
            // If photo is requested, show a 'В разработке' popup instead of selecting
            if (format === 'photo') {
                createDevPopup('Фото: в разработке');
                return;
            }

            selectedFormat = format;
            document.querySelectorAll('.format-btn').forEach(btn => {
                btn.classList.remove('active');
            });
            document.querySelector(`[data-format="${format}"]`).classList.add('active');
            
            const qualitySelector = document.getElementById('qualitySelector');
            const urlInput = document.getElementById('url');
            if (format === 'video') {
                qualitySelector.style.display = 'block';
                urlInput.placeholder = 'https://www.youtube.com/watch?v=...';
            } else if (format === 'audio') {
                qualitySelector.style.display = 'none';
                urlInput.placeholder = 'https://www.tiktok.com/@username/video/1234567890123456789';
            } else if (format === 'photo') {
                qualitySelector.style.display = 'none';
                urlInput.placeholder = 'https://www.pinterest.com/pin/123456789012345678/';
            }
        }
        
        async function startDownload() {
            const url = document.getElementById('url').value.trim();
            if (!url) {
                showStatus('Пожалуйста, введите ссылку на видео', 'error');
                return;
            }
            const quality = document.getElementById('quality').value;
            
            const downloadBtn = document.querySelector('.download-btn');
            
            downloadBtn.disabled = true;
            downloadBtn.innerHTML = '⏳ Обработка...<span class="spinner"></span>';
            
            document.getElementById('progressContainer').style.display = 'block';
            document.getElementById('progressFill').style.width = '0%';
            document.getElementById('progressFill').textContent = '0%';
            document.getElementById('mediaPlayer').style.display = 'none';
            document.getElementById('statusMessage').style.display = 'none';
            
            try {
                // audio track selection removed: let yt-dlp pick defaults
                const checkDownloaded = document.getElementById('checkDownloaded') ? document.getElementById('checkDownloaded').checked : true;

                // persist choices so reload doesn't lose them
                try {
                    localStorage.setItem('dv_url', url);
                    localStorage.setItem('dv_format', selectedFormat);
                    localStorage.setItem('dv_quality', quality);
                } catch (e) { console.warn('localStorage not available', e); }

                const response = await fetch('/download', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ url: url, format: selectedFormat, quality: quality, check_downloaded: checkDownloaded })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    downloadId = data.download_id;
                    document.getElementById('cancelBtn').style.display = 'block';
                    startProgressTracking();
                } else {
                    showStatus(data.error || 'Ошибка при скачивании', 'error');
                    downloadBtn.disabled = false;
                    downloadBtn.innerHTML = '⬇️ Скачать';
                    document.getElementById('progressContainer').style.display = 'none';
                }
            } catch (error) {
                showStatus('Ошибка сети: ' + error.message, 'error');
                downloadBtn.disabled = false;
                downloadBtn.innerHTML = '⬇️ Скачать';
                document.getElementById('progressContainer').style.display = 'none';
            }
        }
        
        function startProgressTracking() {
            progressInterval = setInterval(async () => {
                try {
                    const response = await fetch(`/progress/${downloadId}`);
                    const data = await response.json();
                    
                    if (data.progress !== undefined) {
                        const progress = Math.round(data.progress);
                        document.getElementById('progressFill').style.width = progress + '%';
                        document.getElementById('progressFill').textContent = progress + '%';
                        
                        if (data.status === 'completed') {
                            clearInterval(progressInterval);
                            document.getElementById('cancelBtn').style.display = 'none';
                            showStatus('✅ Скачивание завершено!', 'success');
                            displayMedia(data.filename, data.format);
                            
                            const downloadBtn = document.querySelector('.download-btn');
                            downloadBtn.disabled = false;
                            downloadBtn.innerHTML = '⬇️ Скачать';
                        } else if (data.status === 'error') {
                            clearInterval(progressInterval);
                            document.getElementById('cancelBtn').style.display = 'none';
                            showStatus('❌ ' + (data.error || 'Ошибка при скачивании'), 'error');
                            
                            const downloadBtn = document.querySelector('.download-btn');
                            downloadBtn.disabled = false;
                            downloadBtn.innerHTML = '⬇️ Скачать';
                            document.getElementById('progressContainer').style.display = 'none';
                        }
                    }
                } catch (error) {
                    console.error('Ошибка получения прогресса:', error);
                }
            }, 500);
        }
        
            async function cancelDownload() {
                if (!downloadId) return;
                try {
                    await fetch(`/cancel/${downloadId}`, { method: 'POST' });
                    showStatus('Отмена запроса отправлена', 'error');
                    document.getElementById('cancelBtn').style.display = 'none';
                } catch (e) {
                    console.error('Ошибка отмены:', e);
                }
            }
        
        function displayMedia(filename, format) {
            const mediaPlayer = document.getElementById('mediaPlayer');
            mediaPlayer.style.display = 'block';
            mediaPlayer.classList.remove('is-vertical'); // Сброс класса

            let mediaHtml = '';
            if (format === 'video') {
                const ext = filename.split('.').pop().toLowerCase();
                const vtype = (ext === 'mp4' || ext === 'm4v') ? 'video/mp4' : (ext === 'webm' ? 'video/webm' : 'video/mp4');
                mediaHtml = `
                    <div class="media-player-container">
                        <video controls autoplay>
                            <source src="/file/${filename}" type="${vtype}">
                            Ваш браузер не поддерживает видео.
                        </video>
                    </div>
                `;
            } else if (format === 'audio') {
                const ext = filename.split('.').pop().toLowerCase();
                const atype = (ext === 'mp3') ? 'audio/mpeg' : (ext === 'm4a' ? 'audio/mp4' : 'audio/mpeg');
                mediaHtml = `
                    <audio controls autoplay>
                        <source src="/file/${filename}" type="${atype}">
                        Ваш браузер не поддерживает аудио.
                    </audio>
                `;
            } else if (format === 'photo') {
                mediaHtml = `
                    <img src="/file/${filename}" alt="photo" style="max-width:100%; max-height:80vh; display:block; margin:auto;">
                `;
            }

            mediaHtml += `
                <button class="download-file-btn" onclick="downloadFile('${filename}')">
                    💾 Сохранить на устройство
                </button>
            `;

            mediaPlayer.innerHTML = mediaHtml;

            if (format === 'video') {
                const video = mediaPlayer.querySelector('video');
                if (video) {
                    video.onloadedmetadata = () => {
                        if (video.videoHeight > video.videoWidth) {
                            mediaPlayer.classList.add('is-vertical');
                        }
                    };
                }
            }
        }
        
        function downloadFile(filename) {
            window.location.href = `/file/${filename}?download=true`;
        }
        
        function showStatus(message, type) {
            const statusEl = document.getElementById('statusMessage');
            // For success: show a transient toast popup; for error: show inline status box
            if (type === 'success') {
                // Only show toast popups for success (no inline green messages)
                createToast(message, 5000);
            } else {
                statusEl.textContent = message;
                statusEl.className = 'status-message ' + type;
                statusEl.style.display = 'block';
            }
        }

        function createToast(message, ttl = 5000) {
            const container = document.getElementById('toastContainer');
            if (!container) return;
            const t = document.createElement('div');
            t.className = 'toast';
            t.style.position = 'relative';
            t.textContent = message;
            // progress line
            const pl = document.createElement('div');
            pl.className = 'progress-line';
            t.appendChild(pl);
            container.appendChild(t);

            // allow interactions on toast itself
            t.style.pointerEvents = 'auto';

            // trigger shrink animation using transition
            pl.style.transition = `width ${ttl}ms linear`;
            // slight delay to ensure element is in DOM
            setTimeout(() => { try { pl.style.width = '0%'; } catch (e) {} }, 50);

            // add fade-out class shortly before removal so CSS animation plays
            setTimeout(() => {
                try { t.classList.add('fade-out'); } catch (e) {}
            }, ttl - 260);
            setTimeout(() => { try { container.removeChild(t); } catch (e) {} }, ttl);
        }

        function createDevPopup(text, ttl = 2000) {
            let dp = document.getElementById('devPopup');
            if (!dp) {
                dp = document.createElement('div');
                dp.id = 'devPopup';
                dp.className = 'dev-popup';
                document.body.appendChild(dp);
            }
            dp.textContent = text;
            dp.classList.add('show');
            setTimeout(() => { dp.classList.remove('show'); }, ttl);
        }
        
        // Инициализация при загрузке
        selectFormat('video');
        // restore persisted values (avoid auto-selecting photo since it's not implemented)
        try {
            const savedUrl = localStorage.getItem('dv_url');
            const savedFormat = localStorage.getItem('dv_format');
            const savedQuality = localStorage.getItem('dv_quality');
            // audio_lang storage removed
            const savedCheck = localStorage.getItem('dv_check_downloaded');
            if (savedUrl) document.getElementById('url').value = savedUrl;
            if (savedFormat && savedFormat !== 'photo') selectFormat(savedFormat);
            if (savedQuality) document.getElementById('quality').value = savedQuality;
            if (savedCheck !== null && document.getElementById('checkDownloaded')) document.getElementById('checkDownloaded').checked = (savedCheck === 'true');
        } catch (e) { /* ignore */ }

        // Populate selects with labels if probe data stored (we require user to press Download to probe)
        
        // Make toast container follow viewport more smoothly on scroll (small parallax nudge)
        try {
            const toastContainer = document.getElementById('toastContainer');
            if (toastContainer) {
                let lastScrollY = window.scrollY;
                window.addEventListener('scroll', () => {
                    const delta = window.scrollY - lastScrollY;
                    // small clamped translate to create a follow feel
                    const maxNudge = 8; // px
                    const nudge = Math.max(-maxNudge, Math.min(maxNudge, -delta * 0.2));
                    toastContainer.style.transform = `translateX(-50%) translateY(${nudge}px)`;
                    // gently reset after short timeout
                    clearTimeout(window._toastNudgeTimeout);
                    window._toastNudgeTimeout = setTimeout(() => {
                        toastContainer.style.transform = 'translateX(-50%) translateY(0px)';
                    }, 180);
                    lastScrollY = window.scrollY;
                }, { passive: true });
            }
        } catch (e) { /* ignore */ }
        
    </script>
</body>
</html>
'''

def log_download(user_ip, url, filename, status):
    """Записывает информацию о загрузке в лог-файл"""
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"[{timestamp}] Пользователь: {user_ip} | Ссылка: {url} | Файл: {filename} | Статус: {status}\n"
    
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_entry)
            f.flush()
    except Exception as e:
        logger.error(f"Ошибка записи в лог: {e}")

def progress_hook(d, download_id):
    """Обработчик прогресса загрузки"""
    # If user requested cancel, raise to abort yt-dlp
    if download_cancelled.get(download_id):
        raise Exception('Загрузка отменена пользователем')
    if d['status'] == 'downloading':
        try:
            # Извлекаем прогресс из строки или числа
            if 'downloaded_bytes' in d and 'total_bytes' in d:
                progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
            elif 'downloaded_bytes' in d and 'total_bytes_estimate' in d:
                progress = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
            elif '_percent_str' in d:
                percent_str = d['_percent_str'].strip().replace('%', '')
                progress = float(percent_str)
            else:
                progress = 0
            download_progress[download_id] = {
                'progress': progress,
                'status': 'downloading'
            }
        except Exception as e:
            logger.error(f"Ошибка обработки прогресса: {e}")
    
    elif d['status'] == 'finished':
        download_progress[download_id]['progress'] = 100
        download_progress[download_id]['status'] = 'processing'

def download_media(url, format_type, quality, download_id, user_ip, check_downloaded=None):
    """Функция загрузки медиа в отдельном потоке"""
    try:
        # Получим метаданные (id/title) через extract_info, без загрузки
        ydl_info_opts = {'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_info_opts) as ydl_info:
            info = ydl_info.extract_info(url, download=False)

        if not info:
            raise Exception("Не удалось получить информацию о видео. Проверьте ссылку.")

        video_id = info.get('id') or str(uuid.uuid4())
        title = re.sub(r"[^0-9A-Za-zА-Яа-я_\- ]+", '', info.get('title') or 'media')
        title_short = title.strip().replace(' ', '_')[:60]

        # Deterministic filename using video id, format and quality
        if format_type == 'audio':
            ext = 'mp3'
            filename = f"audio_{video_id}_{title_short}.{ext}"
        elif format_type == 'photo':
            # photos we'll save as JPGs
            ext = 'jpg'
            filename = f"photo_{video_id}_{title_short}.{ext}"
        else:
            ext = 'mp4'
            filename = f"video_{video_id}_{title_short}_{quality}.{ext}"

        filepath = DOWNLOADS_DIR / filename

    # If file already exists, skip download and mark completed
        meta_file = filepath.with_suffix(filepath.suffix + '.json')
        # determine whether to honor existing files for this download (per-request overrides global)
        effective_check = CHECK_DOWNLOADED if check_downloaded is None else bool(check_downloaded)

        if filepath.exists():
            if effective_check:
                # Populate progress as completed and include metadata if available
                download_progress[download_id] = {
                    'progress': 100,
                    'status': 'completed',
                    'filename': filepath.name,
                    'format': format_type
                }
                # Log detailed info
                log_entry = (
                    f"[{datetime.datetime.now().isoformat()}] SKIP: file exists | user: {user_ip} | url: {url} | "
                    f"file: {filepath.name} | size: {filepath.stat().st_size}"
                )
                try:
                    with open(LOG_FILE, 'a', encoding='utf-8') as lf:
                        lf.write(log_entry + '\n')
                except Exception:
                    pass
                return
            else:
                # When CHECK_DOWNLOADED is False, remove existing file and proceed to redownload
                try:
                    existing_size = filepath.stat().st_size
                    filepath.unlink()
                    # Also try to remove metadata file if exists
                    try:
                        if meta_file.exists():
                            meta_file.unlink()
                    except Exception:
                        pass
                    with open(LOG_FILE, 'a', encoding='utf-8') as lf:
                        lf.write(f"[{datetime.datetime.now().isoformat()}] REDOWNLOAD: removed existing file ({existing_size} bytes) | user: {user_ip} | url: {url} | file: {filepath.name}\n")
                except Exception as e:
                    # If we couldn't remove, still try to continue but log
                    try:
                        with open(LOG_FILE, 'a', encoding='utf-8') as lf:
                            lf.write(f"[{datetime.datetime.now().isoformat()}] WARNING: failed to remove existing file: {e} | file: {filepath.name}\n")
                    except Exception:
                        pass

        # Handle photo format: try to download thumbnail or image URL from info
        if format_type == 'photo':
            # info may contain thumbnails
            thumbnail_url = None
            if 'thumbnail' in info and info['thumbnail']:
                thumbnail_url = info['thumbnail']
            else:
                # try thumbnails list
                thumbs = info.get('thumbnails') or info.get('thumbnails')
                if thumbs and isinstance(thumbs, list) and len(thumbs) > 0:
                    thumbnail_url = thumbs[-1].get('url')

            # If no thumbnail found via yt-dlp metadata, attempt site-specific scrape for Pinterest images
            def try_pinterest_image_download(page_url, out_path):
                try:
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                    r = requests.get(page_url, headers=headers, timeout=15)
                    r.raise_for_status()
                    html = r.text
                    # Try og:image
                    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
                    if not m:
                        # Look for JSON LD or initial state with images
                        m = re.search(r'"images":\s*\[\s*\{[^\}]*"url":"([^"]+)"', html)
                    if not m:
                        # fallback to first <img src=...> in the pin content area
                        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*class=["\'][^"\']*pinImage[^"\']*["\']', html, re.I)
                    if not m:
                        # more generic img tag fallback
                        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I)
                    if m:
                        img_url = m.group(1)
                        img_url = img_url.replace('\\u0026', '&').replace('\\/', '/')
                        # try to download
                        rr = requests.get(img_url, headers=headers, stream=True, timeout=30)
                        rr.raise_for_status()
                        with open(out_path, 'wb') as ofh:
                            for chunk in rr.iter_content(8192):
                                if chunk:
                                    ofh.write(chunk)
                        # write metadata
                        try:
                            metadata = {
                                'url': page_url,
                                'downloaded_at': datetime.datetime.now().isoformat(),
                                'method': 'pinterest_image_scrape',
                                'source_image': img_url
                            }
                            with open(out_path.with_suffix(out_path.suffix + '.json'), 'w', encoding='utf-8') as mf:
                                json.dump(metadata, mf, ensure_ascii=False, indent=2)
                        except Exception:
                            pass
                        return True
                    return False
                except Exception as e:
                    logger.info(f'Pinterest image scrape failed: {e}')
                    return False

            if thumbnail_url:
                try:
                    resp = requests.get(thumbnail_url, timeout=15)
                    resp.raise_for_status()
                    with open(filepath, 'wb') as f:
                        f.write(resp.content)

                    # write metadata
                    metadata = {
                        'url': url,
                        'title': info.get('title'),
                        'id': video_id,
                        'format': 'photo',
                        'downloaded_at': datetime.datetime.now().isoformat(),
                        'source_thumbnail': thumbnail_url,
                        'file_size': filepath.stat().st_size
                    }
                    try:
                        with open(meta_file, 'w', encoding='utf-8') as mf:
                            json.dump(metadata, mf, ensure_ascii=False, indent=2)
                    except Exception:
                        pass

                    download_progress[download_id] = {
                        'progress': 100,
                        'status': 'completed',
                        'filename': filepath.name,
                        'format': 'photo'
                    }
                    with open(LOG_FILE, 'a', encoding='utf-8') as lf:
                        lf.write(f"[{datetime.datetime.now().isoformat()}] PHOTO DOWNLOADED | user: {user_ip} | url: {url} | file: {filepath.name} | size: {filepath.stat().st_size}\n")
                    return
                except Exception as e:
                    raise Exception(f"Не удалось скачать изображение: {e}")
            else:
                # If URL looks like Pinterest, try scraping the page for image URL and download
                if ('pinterest.com' in url.lower() or 'pin.it' in url.lower()):
                    # ensure photo filenames use .jpg
                    out_path = filepath.with_suffix('.jpg')
                    success = try_pinterest_image_download(url, out_path)
                    if success and out_path.exists():
                        download_progress[download_id] = {
                            'progress': 100,
                            'status': 'completed',
                            'filename': out_path.name,
                            'format': 'photo'
                        }
                        with open(LOG_FILE, 'a', encoding='utf-8') as lf:
                            lf.write(f"[{datetime.datetime.now().isoformat()}] PINTEREST PHOTO SCRAPED | user: {user_ip} | url: {url} | file: {out_path.name}\n")
                        return
                raise Exception('Не найден URL изображения для данного ресурса')
        
    # Настройки yt-dlp
        ydl_opts = {
            'outtmpl': str(filepath.with_suffix('.%(ext)s')),
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [lambda d: progress_hook(d, download_id)],
            'nopart': True,  # Избегаем частичных файлов, чтобы предотвратить ошибку 416
        }
        # Try to locate ffmpeg and tell yt-dlp where it is (helps when ffmpeg isn't on PATH)
        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path:
            # yt-dlp accepts 'ffmpeg_location' pointing to ffmpeg binary or folder
            # provide the parent folder so yt-dlp can find all helpers
            ydl_opts['ffmpeg_location'] = str(Path(ffmpeg_path).parent)
            logger.info(f"Using ffmpeg at: {ffmpeg_path}")
        else:
            logger.info("ffmpeg not found in PATH or local folder; merging formats may fail.")

        if format_type == 'audio':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
            # audio_lang option removed — yt-dlp will pick default audio
        else:
            # Для YouTube: выбираем bestvideo+bestaudio с максимальным битрейтом и mp4
            # Формат: bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/best
            # Для других сайтов fallback на старую схему
            if 'youtube.com' in url or 'youtu.be' in url:
                quality_map = {
                    '1080': 'bestvideo[height<=1080][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/best',
                    '720': 'bestvideo[height<=720][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/best',
                    '480': 'bestvideo[height<=480][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/best',
                    '360': 'bestvideo[height<=360][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/best',
                }
                # If ffmpeg is missing, avoid requesting a merge of separate video+audio since that will fail.
                if ffmpeg_path:
                    ydl_opts.update({
                        'format': quality_map.get(quality, 'bestvideo+bestaudio/best'),
                        'merge_output_format': 'mp4',
                    })
                else:
                    # Fallback: request the best single file (may already contain combined streams)
                    logger.warning('ffmpeg not found — falling back to best single-file format to avoid merge error')
                    ydl_opts.update({
                        'format': 'best',
                    })
            else:
                # Для TikTok и других сайтов, где может потребоваться перекодирование,
                # используем postprocessor для гарантии mp4.
                if 'tiktok.com' in url:
                    ydl_opts.update({
                        'format': 'bestvideo+bestaudio/best',
                        'postprocessors': [{
                            'key': 'FFmpegVideoConvertor',
                            'preferedformat': 'mp4',
                        }],
                        'merge_output_format': 'mp4',
                    })
                    # Если ffmpeg нет, то слияние и конвертация не сработают, поэтому лучше выбрать лучший одиночный файл
                    if not ffmpeg_path:
                        logger.warning('ffmpeg not found — falling back to best single-file format for TikTok')
                        ydl_opts.pop('postprocessors', None)
                        ydl_opts['format'] = 'best'
                else:
                    quality_map = {
                        '1080': 'best[height<=1080]/bestvideo[height<=1080]+bestaudio/best',
                        '720': 'best[height<=720]/bestvideo[height<=720]+bestaudio/best',
                        '480': 'best[height<=480]/bestvideo[height<=480]+bestaudio/best',
                        '360': 'best[height<=360]/bestvideo[height<=360]+bestaudio/best',
                    }
                    if ffmpeg_path:
                        ydl_opts.update({
                            'format': quality_map.get(quality, 'best'),
                            'merge_output_format': 'mp4',
                        })
                    else:
                        logger.warning('ffmpeg not found — falling back to best single-file format to avoid merge error')
                        ydl_opts.update({
                            'format': 'best',
                        })

        # Special-case tweaks for Pinterest which sometimes lacks normal formats
        try:
            if 'pinterest.com' in url.lower() or 'pin.it' in url.lower():
                # try settings that help with HLS / unusual containers
                ydl_opts.update({
                    'hls_prefer_native': True,
                    'allow_unplayable_formats': True,
                    # try prefer native best as initial attempt
                    'format': ydl_opts.get('format', 'best')
                })
                logger.info('Applying Pinterest-friendly yt-dlp options')
        except Exception:
            pass

        # Helper: try to scrape a direct video URL from a Pinterest page and download it
        def try_pinterest_direct_download(page_url, out_path):
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                resp = requests.get(page_url, headers=headers, timeout=15)
                resp.raise_for_status()
                html = resp.text
                # Try common meta tags
                m = re.search(r'<meta[^>]+property=["\']og:video(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
                if not m:
                    # video tag
                    m = re.search(r'<video[^>]+src=["\']([^"\']+)["\']', html, re.I)
                if not m:
                    # JSON 'playable_url' or 'contentUrl'
                    m = re.search(r'"playable_url":"([^"]+)"', html)
                if not m:
                    m = re.search(r'"contentUrl":"([^"]+)"', html)
                if m:
                    video_url = m.group(1)
                    # unescape if needed
                    video_url = video_url.replace('\\u0026', '&').replace('\\/', '/')
                    logger.info(f'Pinterest direct video URL found: {video_url}')
                    # Stream download
                    r2 = requests.get(video_url, headers=headers, stream=True, timeout=30)
                    r2.raise_for_status()
                    total = r2.headers.get('content-length')
                    with open(out_path, 'wb') as fh:
                        if total is None:
                            fh.write(r2.content)
                        else:
                            dl = 0
                            total_i = int(total)
                            for chunk in r2.iter_content(chunk_size=8192):
                                if chunk:
                                    fh.write(chunk)
                                    dl += len(chunk)
                                    download_progress[download_id] = {'progress': (dl / total_i) * 100, 'status': 'downloading'}
                    # write metadata
                    try:
                        metadata = {
                            'url': page_url,
                            'downloaded_at': datetime.datetime.now().isoformat(),
                            'method': 'pinterest_direct'
                        }
                        with open(out_path.with_suffix(out_path.suffix + '.json'), 'w', encoding='utf-8') as mf:
                            json.dump(metadata, mf, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
                    return True
                return False
            except Exception as e:
                logger.info(f'Pinterest direct download attempt failed: {e}')
                return False
        
        # audio_lang handling removed (no per-request audio track selection)

        # Инициализация прогресса
        download_progress[download_id] = {
            'progress': 0,
            'status': 'downloading'
        }
        
        # If this looks like a Pinterest URL and the user requested video, try direct scraping/download first
        try:
            if format_type == 'video' and ('pinterest.com' in url.lower() or 'pin.it' in url.lower()):
                # use filepath as Path
                out_path = filepath
                success = try_pinterest_direct_download(url, out_path)
                if success and out_path.exists():
                    download_progress[download_id] = {
                        'progress': 100,
                        'status': 'completed',
                        'filename': out_path.name,
                        'format': 'video'
                    }
                    with open(LOG_FILE, 'a', encoding='utf-8') as lf:
                        lf.write(f"[{datetime.datetime.now().isoformat()}] PINTEREST DIRECT DOWNLOADED | user: {user_ip} | url: {url} | file: {out_path.name}\n")
                    return
        except Exception:
            # ignore and continue to yt-dlp
            pass

        # Загрузка with retry strategy for 'No video formats found' cases
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as e:
            err = str(e)
            # If yt-dlp couldn't find formats (common on some Pinterest pins), try a relaxed fallback
            if 'no video formats found' in err.lower() or 'unable to extract' in err.lower():
                logger.warning('Primary download failed with format error; retrying with relaxed options')
                try:
                    fallback_opts = dict(ydl_opts)
                    fallback_opts.update({
                        'format': 'best',
                        'allow_unplayable_formats': True,
                        'hls_prefer_native': True,
                        'ignoreerrors': True,
                    })
                    with yt_dlp.YoutubeDL(fallback_opts) as ydl2:
                        ydl2.download([url])
                except Exception as e2:
                    # rethrow original error if fallback failed
                    raise Exception(f"Fallback download failed: {e2}")
            else:
                raise

        # После успешной загрузки — найдём файл (по шаблону имени)
        downloaded_file = None
        # prefer exact filename
        if filepath.exists():
            downloaded_file = filepath
        else:
            for file in DOWNLOADS_DIR.glob(f"{filepath.stem}.*"):
                if file.is_file():
                    downloaded_file = file
                    break

        if downloaded_file:
            final_filename = downloaded_file.name
            # Write metadata file with info for future checks
            metadata = {
                'url': url,
                'title': info.get('title') if info else None,
                'uploader': info.get('uploader') if info else None,
                'id': video_id,
                'format': format_type,
                'quality_requested': quality,
                'downloaded_at': datetime.datetime.now().isoformat(),
                'file_size': downloaded_file.stat().st_size
            }
            try:
                with open(meta_file, 'w', encoding='utf-8') as mf:
                    json.dump(metadata, mf, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning(f"Не удалось записать metadata JSON: {e}")

            download_progress[download_id] = {
                'progress': 100,
                'status': 'completed',
                'filename': final_filename,
                'format': format_type
            }
            # Detailed logging
            log_entry = (
                f"[{datetime.datetime.now().isoformat()}] SUCCESS | user: {user_ip} | url: {url} | "
                f"file: {final_filename} | size: {downloaded_file.stat().st_size} | format: {format_type} | quality: {quality}"
            )
            try:
                with open(LOG_FILE, 'a', encoding='utf-8') as lf:
                    lf.write(log_entry + '\n')
            except Exception:
                pass
        else:
            raise Exception("Файл не найден после загрузки")
        
        # Поиск скачанного файла
        downloaded_file = None
        for file in DOWNLOADS_DIR.glob(f"{filename.split('.')[0]}.*"):
            if file.is_file():
                downloaded_file = file
                break
        
        if downloaded_file:
            final_filename = downloaded_file.name
            download_progress[download_id] = {
                'progress': 100,
                'status': 'completed',
                'filename': final_filename,
                'format': format_type
            }
            log_download(user_ip, url, final_filename, 'успешно')
        else:
            raise Exception("Файл не найден после загрузки")
            
    except Exception as e:
        error_msg = str(e)
        # Detect common ffmpeg / merge errors and make the message more actionable
        lower_err = error_msg.lower()
        if 'ffmpeg' in lower_err or 'merging formats' in lower_err or 'requested merging of multiple formats' in lower_err:
            error_msg = (
                error_msg +
                '\n\nДополнительная информация: Похоже, что ffmpeg не доступен. ' \
                'Разархивируйте ffmpeg рядом с этим скриптом или добавьте его в PATH. ' \
                "Например: put the 'bin' folder path (that contains ffmpeg.exe) into PATH or keep it in 'ffmpeg-7.1.1-essentials_build/bin'."
            )

        logger.error(f"Ошибка загрузки: {error_msg}")
        download_progress[download_id] = {
            'progress': 0,
            'status': 'error',
            'error': error_msg
        }
        log_download(user_ip, url, 'error', f'ошибка: {error_msg}')

@app.route('/')
def index():
    """Главная страница"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/download', methods=['POST'])
def download():
    """Обработка запроса на загрузку"""
    try:
        data = request.json
        if data is None:
            return jsonify({'success': False, 'error': 'Некорректный JSON в запросе'})
        url = data.get('url')
        format_type = data.get('format', 'video')
        quality = data.get('quality', '720')
        
        if not url:
            return jsonify({'success': False, 'error': 'URL не указан'})
        
        # Генерация ID загрузки
        download_id = str(uuid.uuid4())
        
        # Получение IP пользователя
        user_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
        
        # Запуск загрузки в отдельном потоке (передаем опции)
        # extract per-request check flag (if sent)
        req_check = data.get('check_downloaded') if isinstance(data, dict) else None

        thread = threading.Thread(
            target=download_media,
            args=(url, format_type, quality, download_id, user_ip, req_check)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'success': True,
            'download_id': download_id
        })
        
    except Exception as e:
        logger.error(f"Ошибка в /download: {e}")
        return jsonify({'success': False, 'error': str(e)})


# /probe route removed: audio-track probing UI was removed


@app.route('/cancel/<download_id>', methods=['POST'])
def cancel_download(download_id):
    """Устанавливает флаг отмены для загрузки"""
    download_cancelled[download_id] = True
    # mark progress as cancelled
    download_progress[download_id] = {
        'progress': 0,
        'status': 'error',
        'error': 'Загрузка отменена пользователем'
    }
    return ('', 204)

@app.route('/progress/<download_id>')
def get_progress(download_id):
    """Получение прогресса загрузки"""
    if download_id in download_progress:
        return jsonify(download_progress[download_id])
    else:
        return jsonify({'progress': 0, 'status': 'waiting'})

@app.route('/file/<filename>')
def serve_file(filename):
    """Отдача файла для просмотра или скачивания"""
    try:
        filepath = DOWNLOADS_DIR / filename
        if not filepath.exists():
            return "Файл не найден", 404
        
        # Проверка, нужно ли форсировать скачивание
        if request.args.get('download') == 'true':
            return send_file(
                filepath,
                as_attachment=True,
                download_name=filename
            )
        else:
            # Для просмотра в браузере: определяем тип через mimetypes
            guessed, _ = mimetypes.guess_type(str(filepath))
            mimetype = guessed or ('video/mp4' if filename.endswith('.mp4') else 'application/octet-stream')
            return send_file(filepath, mimetype=mimetype)
            
    except Exception as e:
        logger.error(f"Ошибка отдачи файла: {e}")
        return "Ошибка при получении файла", 500

@app.errorhandler(404)
def not_found(e):
    """Обработка 404 ошибки"""
    return "Страница не найдена", 404

@app.errorhandler(500)
def server_error(e):
    """Обработка 500 ошибки"""
    return "Внутренняя ошибка сервера", 500

if __name__ == '__main__':
    # Создание папки для загрузок если её нет
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    
    print("""
    ╔════════════════════════════════════════════╗
    ║   Universal Video Downloader Has Started   ║
    ║                                            ║
    ║   Поддерживаемые сайты:                    ║
    ║   • YouTube                                ║
    ║   • TikTok                                 ║
    ║   • Instagram                              ║
    ║   • Facebook                               ║
    ║   • И многие другие...                     ║
    ║                                            ║
    ║   Откройте в браузере:                     ║
    ║   http://127.0.0.1:5000                    ║
    ╚════════════════════════════════════════════╝
    """)
    
    # Запуск сервера
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)
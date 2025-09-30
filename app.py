import cv2
import json
import os
import sys
import time
import threading
import gc
from contextlib import contextmanager
from flask import Flask, render_template, Response, request, jsonify
from flask_cors import CORS

# --- SOZLAMALAR ---
CONFIG_FILE = 'config.json'
POLYGONS_DIR = 'polygons_data'
# Har necha soniyada yangi rasm olinishi (1 soat = 3600 soniya)
IMAGE_REFRESH_INTERVAL_SECONDS = 3600
# RTSP oqimidan tiniq kadr olish uchun o'tkazib yuboriladigan kadrlar soni
FRAMES_TO_SKIP = 10
# ------------------

os.makedirs(POLYGONS_DIR, exist_ok=True)
app = Flask(__name__)
CORS(app)


# --- FFMPEG XATOLIKLARINI YASHIRISH UCHUN FUNKSIYA ---
@contextmanager
def suppress_stderr():
    """
    Bu kontekst menejeri ichidagi har qanday operatsiya vaqtida
    chiqarilgan barcha xatoliklarni (stderr) vaqtinchalik o'chirib turadi.
    """
    # Platformaga mos bo'sh fayl manzilini tanlaymiz
    null_fd_path = os.devnull
    # Fayl deskriptorlarini saqlab qolamiz
    original_stderr_fd = None
    try:
        # Asl stderr oqimini eslab qolamiz
        original_stderr_fd = os.dup(sys.stderr.fileno())
        # Bo'sh faylni ochamiz
        with open(null_fd_path, 'w') as null_file:
            # stderr ni bo'sh faylga yo'naltiramiz
            os.dup2(null_file.fileno(), sys.stderr.fileno())

        # Asosiy kodni ishga tushirishga ruxsat beramiz
        yield
    finally:
        # Ish tugagach, stderr ni har doim asl holatiga qaytaramiz
        if original_stderr_fd is not None:
            os.dup2(original_stderr_fd, sys.stderr.fileno())
            os.close(original_stderr_fd)


# -----------------------------------------------------------

# --- CAMERA SINFI ---
class Camera:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.frame = None
        self.frame_width = 0
        self.frame_height = 0
        self.lock = threading.Lock()
        self.is_running = True
        self.thread = threading.Thread(target=self._updater)
        self.thread.daemon = True
        self._update_frame()
        self.thread.start()

    def _get_latest_frame_from_rtsp(self, skip_frames=5):
        """RTSP oqimidan bitta tiniq kadr oladi, ffmpeg xatoliklarini yashirgan holda."""
        cap = None
        frame = None
        ret = False
        try:
            # Xatoliklarni yashiradigan blok ichida kamerani ochamiz
            with suppress_stderr():
                cap = cv2.VideoCapture(self.rtsp_url)

            if not cap.isOpened():
                print(f"Xatolik: Kameraga ulanib bo'lmadi - {self.rtsp_url}")
                return None

            # Buferni tozalash
            for _ in range(skip_frames):
                with suppress_stderr():
                    cap.grab()

            # Asosiy kadrni o'qish
            with suppress_stderr():
                ret, frame = cap.read()

            return frame if ret else None
        except Exception as e:
            print(f"Kadr olishda kutilmagan xatolik: {e}")
            return None
        finally:
            if cap is not None:
                cap.release()
            gc.collect()

    def _update_frame(self):
        """Kameradan yangi rasm olib, uni saqlaydi."""
        print(f"Kameradan yangi rasm olinmoqda: {self.rtsp_url}")
        frame = self._get_latest_frame_from_rtsp(skip_frames=FRAMES_TO_SKIP)
        if frame is not None:
            with self.lock:
                self.frame = frame
                if self.frame_width == 0:
                    self.frame_height, self.frame_width, _ = frame.shape
                    print(f"O'lcham aniqlandi: {self.frame_width}x{self.frame_height}")
        else:
            print(f"Kameradan rasm olib bo'lmadi: {self.rtsp_url}")

    def _updater(self):
        """Har belgilangan vaqtda _update_frame funksiyasini chaqirib turadi."""
        while self.is_running:
            time.sleep(IMAGE_REFRESH_INTERVAL_SECONDS)
            self._update_frame()

    def get_jpeg_frame(self):
        """Saqlangan kadrni JPEG formatiga o'giradi."""
        with self.lock:
            if self.frame is None: return None
            ret, buffer = cv2.imencode('.jpg', self.frame)
            return buffer.tobytes() if ret else None

    def get_frame_dimensions(self):
        """Kadr o'lchamlarini qaytaradi."""
        for _ in range(50):  # 5 soniya kutish
            if self.frame_width > 0:
                break
            time.sleep(0.1)
        return self.frame_width, self.frame_height


# --- Dasturning Asosiy Qismi ---
cameras = {}
camera_configs = []
try:
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        camera_configs = json.load(f)
    for config in camera_configs:
        cameras[config['id']] = Camera(config['rtsp_url'])
    print(f"{len(cameras)} ta kamera muvaffaqiyatli ishga tushirildi.")
except Exception as e:
    print(f"Xatolik: {CONFIG_FILE} faylini o'qib bo'lmadi. Xatolik: {e}")


# --- Flask uchun yo'llar (Routes) ---

@app.route('/')
def index():
    initial_camera_id = request.args.get('camera', None)
    return render_template('index.html', initial_camera=initial_camera_id,
                           refresh_interval=IMAGE_REFRESH_INTERVAL_SECONDS * 1000)


@app.route('/api/cameras')
def get_cameras():
    return jsonify(camera_configs)


@app.route('/video_feed/<string:camera_id>')
def video_feed(camera_id):
    """Bitta JPEG rasm qaytaradi."""
    camera = cameras.get(camera_id)
    if not camera:
        return "Kamera topilmadi", 404

    frame_bytes = camera.get_jpeg_frame()
    if frame_bytes is None:
        return "Rasm mavjud emas", 404

    return Response(frame_bytes, mimetype='image/jpeg')


@app.route('/api/polygons/<string:camera_id>', methods=['GET', 'POST'])
def handle_polygons(camera_id):
    polygon_file = os.path.join(POLYGONS_DIR, f'polygons_{camera_id}.json')
    if request.method == 'GET':
        try:
            polygons_data = []
            if os.path.exists(polygon_file):
                with open(polygon_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if content: polygons_data = json.loads(content)

            cam = cameras.get(camera_id)
            width, height = (0, 0)
            if cam:
                width, height = cam.get_frame_dimensions()

            return jsonify({
                "polygons": polygons_data,
                "source_frame_size": {"width": width, "height": height}
            })
        except Exception as e:
            return jsonify({"polygons": [], "source_frame_size": None, "error": str(e)})

    if request.method == 'POST':
        data_to_save = request.get_json()
        try:
            with open(polygon_file, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, indent=2, ensure_ascii=False)
            return jsonify(status='success', message=f"'{camera_id}' uchun hududlar saqlandi!")
        except Exception as e:
            return jsonify(status='error', message=str(e)), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
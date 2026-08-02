# This file is part of Limey.
# Copyright (c) 2025-Present Limey
#
# Limey is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with Limey. If not, see <https://www.gnu.org/licenses/>.


"""
Author: Limey
Limey - https://github.com/cubiced0/owo-discord-bot
"""



import os
import io
import asyncio
import aiohttp
import subprocess
import sys

try:
    import numpy as np
except ImportError:
    np = None

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import onnxruntime
except ImportError:
    onnxruntime = None


# Credit to Owo-Dusk for onnxmodel https://github.com/owo-dusk/owo-dusk/blob/main/utils/captcha_solver/best.onnx


_AI_DEPS_ATTEMPTED = False


def _ai_deps_present():
    return np is not None and Image is not None and onnxruntime is not None


def _ensure_ai_deps():
    """
    Try to pip-install any missing AI solver deps (numpy/Pillow/onnxruntime)
    using the current interpreter. Attempts only once per process.
    Returns True if all deps are importable afterwards.
    """
    global np, Image, onnxruntime, _AI_DEPS_ATTEMPTED

    if _ai_deps_present():
        return True

    if _AI_DEPS_ATTEMPTED:
        return False
    _AI_DEPS_ATTEMPTED = True

    missing = []
    if np is None:
        missing.append("numpy")
    if Image is None:
        missing.append("Pillow")
    if onnxruntime is None:
        missing.append("onnxruntime")

    for pkg in missing:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--no-cache-dir"])
        except Exception:
            return False

    # Re-import now that the packages should be on disk
    try:
        import numpy as _np
        np = _np
    except ImportError:
        np = None
    try:
        from PIL import Image as _Image
        Image = _Image
    except ImportError:
        Image = None
    try:
        import onnxruntime as _ort
        onnxruntime = _ort
    except ImportError:
        onnxruntime = None

    return _ai_deps_present()


class CaptchaSolver:
    """
    uses local onnx models to solve 'letterword' security captchas.
    """
    def __init__(self, bot):
        self.bot = bot
        self.model_path = os.path.join(self.bot.base_dir, "models", "best.onnx")
        self.onnx_session = None
        self.classes = "abcdefghijklmnopqrstuvwxyz"
        self.conf_threshold = 0.3
        self.img_size = 384

        if _ai_deps_present():
            self._load_model()
        else:
            self.bot.log("SYS", "AI Solver deps (numpy/Pillow/onnxruntime) missing – attempting auto-install...")
            if _ensure_ai_deps():
                self.bot.log("SYS", "AI Solver deps installed. Loading model...")
                self._load_model()
            else:
                self.bot.log("SYS", "AI Solver deps could not be installed. AI Solver disabled.")

    def _load_model(self):
        if not os.path.exists(self.model_path):
            self.bot.log("ERROR", f"AI Model not found at {self.model_path}")
            return

        try:
            self.onnx_session = onnxruntime.InferenceSession(
                self.model_path,
                providers=["CPUExecutionProvider"]
            )
            self.bot.log("SYS", "AI Captcha Solver initialized.")
        except Exception as e:
            self.bot.log("ERROR", f"Failed to load AI model: {e}")

    def _letterbox(self, img_array, new_size=384, color=(114, 114, 114)):
        """resize image with padding to maintain aspect ratio."""
        img = Image.fromarray(img_array)
        w, h = img.size
        scale = min(new_size / w, new_size / h)
        nw, nh = int(w * scale), int(h * scale)
        img_resized = img.resize((nw, nh), Image.BILINEAR)
        
        new_img = Image.new("RGB", (new_size, new_size), color)
        paste_x = (new_size - nw) // 2
        paste_y = (new_size - nh) // 2
        new_img.paste(img_resized, (paste_x, paste_y))
        
        return np.array(new_img)

    async def solve_image(self, url, letter_count=5):
        """
        downloads a captcha image from a url and predicts the letters.
        """
        if not self.onnx_session:
            return None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return None
                    
                    data = await resp.read()
                    img = Image.open(io.BytesIO(data)).convert("RGB")
                    img_array = np.array(img)
        except Exception as e:
            self.bot.log("ERROR", f"Failed to download captcha image: {e}")
            return None

        img = self._letterbox(img_array, self.img_size)
        img = img.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1)) 
        img = np.expand_dims(img, axis=0) 

        input_name = self.onnx_session.get_inputs()[0].name
        outputs = self.onnx_session.run(None, {input_name: img})[0]
        detections_raw = outputs[0]

        detections = []
        for det in detections_raw:
            x1, y1, x2, y2, conf, cls_id = det
            if conf < self.conf_threshold:
                continue
            
            detections.append({
                "char": self.classes[int(cls_id)],
                "conf": float(conf),
                "cx": float((x1 + x2) / 2)
            })

        if len(detections) > letter_count:
            detections.sort(key=lambda d: d["conf"], reverse=True)
            detections = detections[:letter_count]
        detections.sort(key=lambda d: d["cx"])
        
        result = "".join(d["char"] for d in detections)
        self.bot.log("SECURITY", f"AI Solver Predicted: {result}")
        return result

def setup_solver(bot):
    return CaptchaSolver(bot)

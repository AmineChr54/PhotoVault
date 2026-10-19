"""ONNX model wrappers: SigLIP 2 (image/text embeddings) and SCRFD + ArcFace (faces).
Runs on DirectML (AMD iGPU) when available, otherwise CPU. Imported only in the worker venv."""
import numpy as np
import onnxruntime as ort
from PIL import Image

from . import paths

ARC_TEMPLATE = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                         [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


def providers(prefer_gpu=True):
    avail = ort.get_available_providers()
    if prefer_gpu and "DmlExecutionProvider" in avail:
        return ["DmlExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def session(path, prefer_gpu=True, low_memory=False):
    so = ort.SessionOptions()
    so.log_severity_level = 3
    if low_memory:
        # No pooled CPU arena / memory pattern: activations are freed after each run instead of being
        # kept for the next one, which bounds resident memory over tens of thousands of photos.
        so.enable_cpu_mem_arena = False
        so.enable_mem_pattern = False
    prov = providers(prefer_gpu)
    if prov[0] == "DmlExecutionProvider":
        so.enable_mem_pattern = False  # required by DirectML
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(str(path), so, providers=prov)


def _norm(x):
    return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-9)


class SigLIP:
    SIZE = 256
    MAXLEN = 64

    def __init__(self, prefer_gpu=True):
        self.vision = session(paths.SIGLIP / "onnx" / "vision_model.onnx", prefer_gpu)
        self._text = None
        self._tok = None
        self.prefer_gpu = prefer_gpu

    def prep(self, img: Image.Image) -> np.ndarray:
        im = img.convert("RGB").resize((self.SIZE, self.SIZE), Image.Resampling.BILINEAR)
        a = np.asarray(im, dtype=np.float32) / 255.0
        a = (a - 0.5) / 0.5
        return a.transpose(2, 0, 1)

    def encode_images(self, batch: np.ndarray) -> np.ndarray:
        out = self.vision.run(["pooler_output"], {"pixel_values": batch})[0]
        return _norm(out)

    def _load_text(self):
        if self._text is None:
            from tokenizers import Tokenizer
            self._tok = Tokenizer.from_file(str(paths.SIGLIP / "tokenizer.json"))
            self._tok.enable_padding(length=self.MAXLEN, pad_id=0, pad_token="<pad>")
            self._tok.enable_truncation(self.MAXLEN)
            # Text model is large (multilingual vocab): CPU is plenty for a single query.
            self._text = session(paths.SIGLIP / "onnx" / "text_model.onnx", prefer_gpu=False)

    def encode_text(self, texts) -> np.ndarray:
        self._load_text()
        enc = self._tok.encode_batch([t.lower() for t in texts])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        out = self._text.run(["pooler_output"], {"input_ids": ids})[0]
        return _norm(out)


def _nms(boxes, scores, thr):
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1 + 1) * np.maximum(0, yy2 - yy1 + 1)
        iou = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[1:][iou <= thr]
    return keep


class Faces:
    DET = 960
    STRIDES = (8, 16, 32)

    def __init__(self, prefer_gpu=True, det_thresh=0.5):
        self.det = session(paths.FACES / "det_10g.onnx", prefer_gpu=False, low_memory=True)  # DirectML breaks SCRFD reshapes
        self.rec = session(paths.FACES / "w600k_r50.onnx", prefer_gpu)
        self.det_name = self.det.get_inputs()[0].name
        self.thresh = det_thresh
        self._anchors = {}

    def _anchor(self, stride, n):
        key = (stride, n)
        if key not in self._anchors:
            g = np.stack(np.mgrid[:n, :n][::-1], axis=-1).astype(np.float32)
            a = (g * stride).reshape(-1, 2)
            self._anchors[key] = np.repeat(a, 2, axis=0)
        return self._anchors[key]

    def detect(self, rgb: np.ndarray, det_size=None):
        """rgb uint8 HxWx3 -> list of (box[4], score, kps[5,2]) in image coords. det_size (multiple of 32)
        overrides the detector input size; the indexer passes a smaller one to retry after a failure."""
        import cv2
        det = det_size or self.DET
        h, w = rgb.shape[:2]
        s = det / max(h, w)
        nh, nw = int(round(h * s)), int(round(w * s))
        canvas = np.zeros((det, det, 3), np.uint8)
        canvas[:nh, :nw] = cv2.resize(rgb, (nw, nh))
        blob = ((canvas.astype(np.float32) - 127.5) / 128.0).transpose(2, 0, 1)[None]
        outs = self.det.run(None, {self.det_name: blob})
        boxes, scores, kpss = [], [], []
        n = len(self.STRIDES)
        for i, st in enumerate(self.STRIDES):
            sc = outs[i].reshape(-1)
            bb = outs[i + n].reshape(-1, 4) * st
            kp = outs[i + 2 * n].reshape(-1, 10) * st
            anc = self._anchor(st, det // st)
            idx = np.where(sc >= self.thresh)[0]
            if idx.size == 0:
                continue
            a = anc[idx]
            b = bb[idx]
            boxes.append(np.stack([a[:, 0] - b[:, 0], a[:, 1] - b[:, 1], a[:, 0] + b[:, 2], a[:, 1] + b[:, 3]], 1))
            k = kp[idx].reshape(-1, 5, 2)
            kpss.append(a[:, None, :] + k)
            scores.append(sc[idx])
        if not boxes:
            return []
        boxes = np.concatenate(boxes) / s
        scores = np.concatenate(scores)
        kpss = np.concatenate(kpss) / s
        keep = _nms(boxes, scores, 0.4)
        return [(boxes[i], float(scores[i]), kpss[i]) for i in keep]

    def embed(self, rgb: np.ndarray, kps: np.ndarray) -> np.ndarray:
        import cv2
        m, _ = cv2.estimateAffinePartial2D(kps.astype(np.float32), ARC_TEMPLATE, method=cv2.LMEDS)
        face = cv2.warpAffine(rgb, m, (112, 112), borderValue=0)
        blob = ((face.astype(np.float32) - 127.5) / 127.5).transpose(2, 0, 1)[None]
        return _norm(self.rec.run(None, {self.rec.get_inputs()[0].name: blob})[0])[0]

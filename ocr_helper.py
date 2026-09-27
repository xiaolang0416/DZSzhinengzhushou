"""
OCR辅助脚本 - 作为子进程运行，避免onnxruntime与PyQt5冲突
用法: python ocr_helper.py <screenshot_path> [x y w h]
输出: 识别到的文本，每行一条
"""
import sys
import os
import cv2
import numpy as np


def main():
    if len(sys.argv) < 2:
        print("ERROR: 请提供截图路径")
        return

    img_path = sys.argv[1]
    if not os.path.exists(img_path):
        print(f"ERROR: 文件不存在: {img_path}")
        return

    img = cv2.imread(img_path)
    if img is None:
        print("ERROR: 无法读取图片")
        return

    if len(sys.argv) >= 6:
        x, y, w, h = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
        img = img[y:y+h, x:x+w]
        if img.size == 0:
            print("ERROR: 裁剪区域无效")
            return

    try:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()
        result, _ = ocr(img)
        if result:
            for item in result:
                text = item[1].strip()
                if text:
                    print(text)
    except Exception as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    main()

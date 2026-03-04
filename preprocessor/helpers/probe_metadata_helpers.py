import cv2

def _detect_black_and_white(input_path, num_samples=5):
    cap = cv2.VideoCapture(input_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    sample_indices = [int(i * total_frames / num_samples) for i in range(num_samples)]

    saturations = []
    for idx in sample_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            continue
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mean_saturation = hsv[:, :, 1].mean()
        saturations.append(mean_saturation)

    cap.release()

    if not saturations:
        return False

    return (sum(saturations) / len(saturations)) < 10.0
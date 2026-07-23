import cv2
import math
import random
import numpy as np
import albumentations as A

try:
    from albumentations.augmentations.geometric.transforms import GridDistortion as _GridDistortion
except ImportError:
    try:
        from albumentations.augmentations.transforms import GridDistortion as _GridDistortion
    except ImportError:
        from albumentations import GridDistortion as _GridDistortion

try:
    from albumentations.augmentations.geometric.functional import generate_grid, remap, normalize_grid_distortion_steps
except ImportError:
    pass

try:
    from albumentations.augmentations.geometric.functional import pad_with_params as _pad_with_params
except ImportError:
    try:
        from albumentations.augmentations.functional import pad_with_params as _pad_with_params
    except ImportError:
        from albumentations import pad_with_params as _pad_with_params

try:
    from albucore import maybe_process_in_chunks as _maybe_process_in_chunks
except ImportError:
    try:
        from albumentations.core.bbox_utils import maybe_process_in_chunks as _maybe_process_in_chunks
    except ImportError:
        def _maybe_process_in_chunks(func, **kwargs):
            return lambda img: func(img, **kwargs)

def safe_rotate_enlarged_img_size(angle, rows, cols):
    angle_rad = math.radians(angle)
    abs_cos = abs(math.cos(angle_rad))
    abs_sin = abs(math.sin(angle_rad))
    new_cols = int(rows * abs_sin + cols * abs_cos)
    new_rows = int(rows * abs_cos + cols * abs_sin)
    return new_rows, new_cols

def keypoint_rotate(keypoint, angle, rows, cols):
    # keypoint is (x, y, angle, scale)
    x, y, a, s = keypoint[:4]
    angle_rad = math.radians(angle)
    # Rotate around center
    center_x, center_y = cols / 2, rows / 2
    x -= center_x
    y -= center_y
    
    # Standard rotation matrix (CCW)
    rx = x * math.cos(angle_rad) + y * math.sin(angle_rad)
    ry = -x * math.sin(angle_rad) + y * math.cos(angle_rad)
    
    return (rx + center_x, ry + center_y, a + angle_rad, s)

def safe_rotate(
    img: np.ndarray,
    angle: int = 0,
    interpolation: int = cv2.INTER_LINEAR,
    value: int = None,
    border_mode: int = cv2.BORDER_REFLECT_101,
):

    old_rows, old_cols = img.shape[:2]

    # getRotationMatrix2D needs coordinates in reverse order (width, height) compared to shape
    image_center = (old_cols / 2, old_rows / 2)

    # Rows and columns of the rotated image (not cropped)
    new_rows, new_cols = safe_rotate_enlarged_img_size(angle=angle, rows=old_rows, cols=old_cols)

    # Rotation Matrix
    rotation_mat = cv2.getRotationMatrix2D(image_center, angle, 1.0)

    # Shift the image to create padding
    rotation_mat[0, 2] += new_cols / 2 - image_center[0]
    rotation_mat[1, 2] += new_rows / 2 - image_center[1]

    # CV2 Transformation function
    warp_affine_fn = _maybe_process_in_chunks(
        cv2.warpAffine,
        M=rotation_mat,
        dsize=(new_cols, new_rows),
        flags=interpolation,
        borderMode=border_mode,
        borderValue=value,
    )

    # rotate image with the new bounds
    rotated_img = warp_affine_fn(img)

    return rotated_img


def keypoint_safe_rotate(keypoint, angle, rows, cols):
    old_rows = rows
    old_cols = cols

    # Rows and columns of the rotated image (not cropped)
    new_rows, new_cols = safe_rotate_enlarged_img_size(angle=angle, rows=old_rows, cols=old_cols)

    col_diff = (new_cols - old_cols) / 2
    row_diff = (new_rows - old_rows) / 2

    # Shift keypoint
    shifted_keypoint = (int(keypoint[0] + col_diff), int(keypoint[1] + row_diff), keypoint[2], keypoint[3])

    # Rotate keypoint
    rotated_keypoint = keypoint_rotate(shifted_keypoint, angle, rows=new_rows, cols=new_cols)

    return rotated_keypoint


class SafeRotate(A.SafeRotate):

    def __init__(
        self,
        limit=90,
        interpolation=cv2.INTER_LINEAR,
        border_mode=cv2.BORDER_REFLECT_101,
        value=None,
        mask_value=None,
        always_apply=False,
        p=0.5,
    ):
        super(SafeRotate, self).__init__(
            limit=limit,
            interpolation=interpolation,
            border_mode=border_mode,
            value=value,
            mask_value=mask_value,
            always_apply=always_apply,
            p=p)

    def apply(self, img, angle=0, interpolation=cv2.INTER_LINEAR, **params):
        return safe_rotate(
            img=img, value=self.value, angle=angle, interpolation=interpolation, border_mode=self.border_mode)

    def apply_to_keypoint(self, keypoint, angle=0, **params):
        return keypoint_safe_rotate(keypoint, angle=angle, rows=params["rows"], cols=params["cols"])


class CropWhite(A.DualTransform):
    
    def __init__(self, value=(255, 255, 255), pad=0, p=1.0):
        super(CropWhite, self).__init__(p=p)
        self.value = value
        self.pad = pad
        assert pad >= 0

    def update_params(self, params, **kwargs):
        super().update_params(params, **kwargs)
        assert "image" in kwargs
        img = kwargs["image"]
        height, width, _ = img.shape
        x = (img != self.value).sum(axis=2)
        if x.sum() == 0:
            return params
        row_sum = x.sum(axis=1)
        top = 0
        while row_sum[top] == 0 and top+1 < height:
            top += 1
        bottom = height
        while row_sum[bottom-1] == 0 and bottom-1 > top:
            bottom -= 1
        col_sum = x.sum(axis=0)
        left = 0
        while col_sum[left] == 0 and left+1 < width:
            left += 1
        right = width
        while col_sum[right-1] == 0 and right-1 > left:
            right -= 1
        # crop_top = max(0, top - self.pad)
        # crop_bottom = max(0, height - bottom - self.pad)
        # crop_left = max(0, left - self.pad)
        # crop_right = max(0, width - right - self.pad)
        # params.update({"crop_top": crop_top, "crop_bottom": crop_bottom,
        #                "crop_left": crop_left, "crop_right": crop_right})
        params.update({"crop_top": top, "crop_bottom": height - bottom,
                       "crop_left": left, "crop_right": width - right})
        return params

    def apply(self, img, crop_top=0, crop_bottom=0, crop_left=0, crop_right=0, **params):
        height, width, _ = img.shape
        img = img[crop_top:height - crop_bottom, crop_left:width - crop_right]
        img = _pad_with_params(
            img, self.pad, self.pad, self.pad, self.pad, border_mode=cv2.BORDER_CONSTANT, value=self.value)
        return img

    def apply_to_keypoint(self, keypoint, crop_top=0, crop_bottom=0, crop_left=0, crop_right=0, **params):
        x, y, angle, scale = keypoint[:4]
        return x - crop_left + self.pad, y - crop_top + self.pad, angle, scale

    def apply_to_keypoints(self, keypoints, **params):
        return [self.apply_to_keypoint(kp, **params) for kp in keypoints]

    def apply_to_bbox(self, bbox, crop_top=0, crop_bottom=0, crop_left=0, crop_right=0, **params):
        # bboxes are [x_min, y_min, x_max, y_max]
        x_min, y_min, x_max, y_max = bbox[:4]
        return x_min - crop_left + self.pad, y_min - crop_top + self.pad, x_max - crop_left + self.pad, y_max - crop_top + self.pad

    def apply_to_bboxes(self, bboxes, **params):
        return [self.apply_to_bbox(bbox, **params) for bbox in bboxes]

    def get_transform_init_args_names(self):
        return ('value', 'pad')


class PadWhite(A.DualTransform):

    def __init__(self, pad_ratio=0.2, p=0.5, value=(255, 255, 255)):
        super(PadWhite, self).__init__(p=p)
        self.pad_ratio = pad_ratio
        self.value = value

    def update_params(self, params, **kwargs):
        super().update_params(params, **kwargs)
        assert "image" in kwargs
        img = kwargs["image"]
        height, width, _ = img.shape
        side = random.randrange(4)
        if side == 0:
            params['pad_top'] = int(height * self.pad_ratio * random.random())
        elif side == 1:
            params['pad_bottom'] = int(height * self.pad_ratio * random.random())
        elif side == 2:
            params['pad_left'] = int(width * self.pad_ratio * random.random())
        elif side == 3:
            params['pad_right'] = int(width * self.pad_ratio * random.random())
        return params

    def apply(self, img, pad_top=0, pad_bottom=0, pad_left=0, pad_right=0, **params):
        height, width, _ = img.shape
        img = _pad_with_params(
            img, pad_top, pad_bottom, pad_left, pad_right, border_mode=cv2.BORDER_CONSTANT, value=self.value)
        return img

    def apply_to_keypoint(self, keypoint, pad_top=0, pad_bottom=0, pad_left=0, pad_right=0, **params):
        x, y, angle, scale = keypoint[:4]
        return x + pad_left, y + pad_top, angle, scale

    def apply_to_keypoints(self, keypoints, **params):
        return [self.apply_to_keypoint(kp, **params) for kp in keypoints]

    def apply_to_bbox(self, bbox, pad_top=0, pad_bottom=0, pad_left=0, pad_right=0, **params):
        x_min, y_min, x_max, y_max = bbox[:4]
        return x_min + pad_left, y_min + pad_top, x_max + pad_left, y_max + pad_top

    def apply_to_bboxes(self, bboxes, **params):
        return [self.apply_to_bbox(bbox, **params) for bbox in bboxes]

    def get_transform_init_args_names(self):
        return ('value', 'pad_ratio')


class SaltAndPepperNoise(A.DualTransform):

    def __init__(self, num_dots, value=(0, 0, 0), p=0.5):
        super().__init__(p)
        self.num_dots = num_dots
        self.value = value

    def apply(self, img, **params):
        height, width, _ = img.shape
        num_dots = random.randrange(self.num_dots + 1)
        for i in range(num_dots):
            x = random.randrange(height)
            y = random.randrange(width)
            img[x, y] = self.value
        return img

    def apply_to_keypoint(self, keypoint, **params):
        return keypoint

    def apply_to_keypoints(self, keypoints, **params):
        return keypoints

    def apply_to_bbox(self, bbox, **params):
        return bbox

    def apply_to_bboxes(self, bboxes, **params):
        return bboxes

    def get_transform_init_args_names(self):
        return ('value', 'num_dots')
    
class ResizePad(A.DualTransform):

    def __init__(self, height, width, interpolation=cv2.INTER_LINEAR, value=(255, 255, 255)):
        super(ResizePad, self).__init__(always_apply=True)
        self.height = height
        self.width = width
        self.interpolation = interpolation
        self.value = value

    def apply(self, img, interpolation=cv2.INTER_LINEAR, **params):
        h, w, _ = img.shape
        img = A.augmentations.geometric.functional.resize(
            img, 
            height=min(h, self.height), 
            width=min(w, self.width), 
            interpolation=interpolation
        )
        h, w, _ = img.shape
        pad_top = (self.height - h) // 2
        pad_bottom = (self.height - h) - pad_top
        pad_left = (self.width - w) // 2
        pad_right = (self.width - w) - pad_left
        img = _pad_with_params(
            img,
            pad_top,
            pad_bottom,
            pad_left,
            pad_right,
            border_mode=cv2.BORDER_CONSTANT,
            value=self.value,
        )
        return img


def normalized_grid_distortion(
        img,
        num_steps,
        xsteps,
        ysteps,
        interpolation=cv2.INTER_LINEAR,
        border_mode=cv2.BORDER_REFLECT_101,
        value=0
):
    height, width = img.shape[:2]

    # compensate for smaller last steps in source image.
    x_step = width // num_steps
    last_x_step = min(width, ((num_steps + 1) * x_step)) - (num_steps * x_step)
    xsteps[-1] *= last_x_step / x_step

    y_step = height // num_steps
    last_y_step = min(height, ((num_steps + 1) * y_step)) - (num_steps * y_step)
    ysteps[-1] *= last_y_step / y_step

    # now normalize such that distortion never leaves image bounds.
    tx = width / math.floor(width / num_steps)
    ty = height / math.floor(height / num_steps)
    xsteps = np.array(xsteps) * (tx / np.sum(xsteps))
    ysteps = np.array(ysteps) * (ty / np.sum(ysteps))

    # do actual distortion.
    map_x, map_y = generate_grid(img.shape[:2], xsteps, ysteps, num_steps)
    return remap(img, map_x, map_y, interpolation, border_mode, value)


class NormalizedGridDistortion(_GridDistortion):
    def apply(self, img, stepsx=(), stepsy=(), interpolation=cv2.INTER_LINEAR, **params):
        return normalized_grid_distortion(img, self.num_steps, stepsx, stepsy, interpolation, self.border_mode,
                                          self.value)

    def apply_to_mask(self, img, stepsx=(), stepsy=(), **params):
        return normalized_grid_distortion(
            img, self.num_steps, stepsx, stepsy, cv2.INTER_NEAREST, self.border_mode, self.mask_value)

    def apply_to_keypoints(self, keypoints, **params):
        return keypoints

    def apply_to_bboxes(self, bboxes, **params):
        return bboxes


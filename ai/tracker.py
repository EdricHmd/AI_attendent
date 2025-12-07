"""
Simple per-face tracker used to maintain temporary identities across frames.

This is an intentionally lightweight tracker (centroid/IoU based) suitable for
short-term tracking inside a single process (subprocess or Flask worker).
It provides a small API:
 - update(detections) -> list of track_id aligned with detections
 - note_match(track_id, student_id, matched_bool) -> bool (should_save)

The tracker keeps per-track counters and only signals `should_save` when a
student match is stable for `confirm_frames` consecutive frames.
"""
from typing import List, Dict, Optional, Tuple
import math


def _iou(boxA: Tuple[int, int, int, int], boxB: Tuple[int, int, int, int]) -> float:
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[0] + boxA[2], boxB[0] + boxB[2])
    yB = min(boxA[1] + boxA[3], boxB[1] + boxB[3])
    interW = max(0, xB - xA)
    interH = max(0, yB - yA)
    interArea = interW * interH
    boxAArea = boxA[2] * boxA[3]
    boxBArea = boxB[2] * boxB[3]
    union = float(boxAArea + boxBArea - interArea)
    if union <= 0:
        return 0.0
    return interArea / union


import logging
logger = logging.getLogger(__name__)


class SimpleTracker:
    def __init__(self, iou_threshold: float = 0.3, max_age: int = 10, confirm_frames: int = 2):
        self.iou_threshold = float(iou_threshold)
        self.max_age = int(max_age)
        self.confirm_frames = int(confirm_frames)
        self.next_id = 1
        # id -> track dict
        self.tracks: Dict[int, Dict] = {}

    def reset(self) -> None:
        """Clear all tracking state so a new session starts fresh.

        Useful when the application switches to a different attendance session;
        this allows the same student to be saved again without requiring a
        process restart or page reload.
        """
        self.tracks.clear()
        self.next_id = 1

    def update(self, detections: List[Tuple[int, int, int, int]]) -> List[int]:
        """Assign detections to existing tracks (by IoU) or create new tracks.

        Returns a list of track_ids aligned with the input detections.
        """
        assigned = [-1] * len(detections)

        # compute IoU matrix and greedily match
        if len(self.tracks) > 0 and len(detections) > 0:
            track_items = list(self.tracks.items())
            iou_matrix = []
            for _, t in track_items:
                row = [ _iou(t['bbox'], det) for det in detections ]
                iou_matrix.append(row)

            # greedy matching: for each track pick best detection if above threshold
            for ti, (track_id, t) in enumerate(track_items):
                best_j = -1
                best_iou = 0.0
                for j, score in enumerate(iou_matrix[ti]):
                    if score > best_iou:
                        best_iou = score
                        best_j = j
                if best_j >= 0 and best_iou >= self.iou_threshold and assigned[best_j] == -1:
                    assigned[best_j] = track_id
                    # update track bbox and age
                    t['bbox'] = detections[best_j]
                    t['frames_since_update'] = 0

        # create tracks for unassigned detections
        for idx, det in enumerate(detections):
            if assigned[idx] == -1:
                tid = self.next_id
                self.next_id += 1
                self.tracks[tid] = {
                    'bbox': det,
                    'frames_since_update': 0,
                    'match_count': 0,
                    'matched_student_id': None,
                    'saved_student_id': None,
                }
                assigned[idx] = tid
                logger.debug('tracker: created track %s bbox=%s', tid, det)

        # age existing tracks (tracks not updated in this frame)
        for tid, t in list(self.tracks.items()):
            # if this track was not assigned to any detection in this frame, increment age
            was_updated = any(assigned_idx == tid for assigned_idx in assigned)
            if not was_updated:
                t['frames_since_update'] = t.get('frames_since_update', 0) + 1
            # prune old tracks
            if t.get('frames_since_update', 0) > self.max_age:
                try:
                    logger.info('tracker: track %s expired after %s frames', tid, t.get('frames_since_update', 0))
                    del self.tracks[tid]
                except KeyError:
                    pass

        return assigned

    def note_match(self, track_id: int, student_id: Optional[int], matched: bool) -> bool:
        """Notify tracker that track `track_id` was matched to `student_id` (or None).

        Returns True if the tracker now considers the match stable enough to save
        (i.e., seen for `confirm_frames` consecutive frames and not already saved).
        """
        t = self.tracks.get(track_id)
        if not t:
            return False

        if not matched or student_id is None:
            # reset match counter
            if t.get('matched_student_id') is not None:
                logger.debug('tracker: track %s lost match for student %s - resetting', track_id, t.get('matched_student_id'))
            t['match_count'] = 0
            t['matched_student_id'] = None
            return False

        # matched to some student
        if t.get('matched_student_id') == student_id:
            t['match_count'] = t.get('match_count', 0) + 1
        else:
            t['matched_student_id'] = student_id
            t['match_count'] = 1

        logger.debug('tracker: track %s matched student %s -> count=%s', track_id, student_id, t['match_count'])
        if t['match_count'] >= self.confirm_frames:
            if t.get('saved_student_id') != student_id:
                t['saved_student_id'] = student_id
                logger.info('tracker: track %s CONFIRMED student=%s (confirm_frames=%s)', track_id, student_id, self.confirm_frames)
                return True
        return False

    def is_confirmed(self, track_id: int, student_id: Optional[int]) -> bool:
        """Return True if the given track is currently confirmed for the student_id."""
        t = self.tracks.get(track_id)
        if not t:
            return False
        if t.get('saved_student_id') == student_id:
            return True
        if t.get('matched_student_id') == student_id and t.get('match_count', 0) >= self.confirm_frames:
            return True
        return False

    def dump_state(self) -> Dict[int, Dict]:
        """Return a shallow copy of tracker state for debugging/inspection."""
        out = {}
        for tid, t in self.tracks.items():
            out[tid] = {
                'bbox': t.get('bbox'),
                'frames_since_update': t.get('frames_since_update'),
                'match_count': t.get('match_count'),
                'matched_student_id': t.get('matched_student_id'),
                'saved_student_id': t.get('saved_student_id'),
            }
        return out

"""MuScriptor の追加学習用：ノートを、モデルが出すトークン列に直す（M4）。

パッケージには「トークン → ノート」（decode）しか入っていないので、その逆を書く。
並びの決まり（MT3 と同じ。muscriptor/events.py の OpenNoteTracker が読む形）:
  [前の区間から鳴り続けている音: program, pitch, ...] tie
  [時刻が進むたび shift] [楽器が変わるたび program] [オン/オフが変わるたび velocity] pitch ...
  ドラムは shift のあとに drum（音高）だけ。最後に EOS。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PitchedNote:
    onset_s: float
    offset_s: float
    pitch: int
    program: int


@dataclass(frozen=True)
class DrumHit:
    time_s: float
    pitch: int


def encode_chunk(
    notes: list[PitchedNote],
    drums: list[DrumHit],
    seek_s: float,
    segment_s: float,
    frame_rate: int,
    token_index: dict[tuple[str, int], int],
    eos_id: int,
) -> list[int]:
    """[seek_s, seek_s + segment_s) の区間のトークン列（EOS まで）。"""
    n_ticks = int(round(segment_s * frame_rate))

    def tick(t: float) -> int:
        return int(round((t - seek_s) * frame_rate))

    tokens: list[int] = []
    # 前の区間から鳴り続けている音
    program_state: int | None = None
    held = sorted({(n.program, n.pitch) for n in notes if tick(n.onset_s) < 0 and tick(n.offset_s) > 0})
    for program, pitch in held:
        if program != program_state:
            tokens.append(token_index[("program", program)])
            program_state = program
        tokens.append(token_index[("pitch", pitch)])
    tokens.append(token_index[("tie", 0)])

    # 区間の中の出来事。同じ時刻では、オフ → オン → ドラム の順
    events: list[tuple[int, int, int, int]] = []  # (tick, 種類 0=オフ 1=オン 2=ドラム, program, pitch)
    for n in notes:
        on, off = tick(n.onset_s), tick(n.offset_s)
        if off <= on:
            off = on + 1
        if 0 <= on < n_ticks:
            events.append((on, 1, n.program, n.pitch))
        if 0 < off < n_ticks and on < n_ticks and off > 0:
            events.append((off, 0, n.program, n.pitch))
    for d in drums:
        t = tick(d.time_s)
        if 0 <= t < n_ticks:
            events.append((t, 2, -1, d.pitch))
    events = sorted(set(events))

    tick_state = 0
    program_state = None
    velocity_state: int | None = None
    for t, kind, program, pitch in events:
        if t != tick_state:
            tokens.append(token_index[("shift", t)])
            tick_state = t
        if kind == 2:
            tokens.append(token_index[("drum", pitch)])
            continue
        if program != program_state:
            tokens.append(token_index[("program", program)])
            program_state = program
        if kind != velocity_state:
            tokens.append(token_index[("velocity", kind)])
            velocity_state = kind
        tokens.append(token_index[("pitch", pitch)])
    tokens.append(eos_id)
    return tokens


def stack_drum_hits(hits: list[tuple[float, str]], gap_s: float) -> list[tuple[float, str]]:
    """同じクラスの打点が gap_s 以内に重なっていたら、最初の1つにまとめる（キックの重ねなど）。"""
    out: list[tuple[float, str]] = []
    last: dict[str, float] = {}
    for t, cls in sorted(hits):
        if cls in last and t - last[cls] <= gap_s:
            continue
        last[cls] = t
        out.append((t, cls))
    return out


def match_onsets(ref: list[tuple[float, int]], est: list[tuple[float, int]], tol_s: float) -> tuple[int, int, int]:
    """(時刻, 音高かクラス番号) の一致を数える。返り値: (一致, 正解の数, 出力の数)。

    同じ音高どうしを、時刻の近い順に1対1で対応させる。
    """
    by_key_ref: dict[int, list[float]] = {}
    by_key_est: dict[int, list[float]] = {}
    for t, k in ref:
        by_key_ref.setdefault(k, []).append(t)
    for t, k in est:
        by_key_est.setdefault(k, []).append(t)
    hit = 0
    for k, r in by_key_ref.items():
        e = sorted(by_key_est.get(k, []))
        r = sorted(r)
        pairs = sorted((abs(a - b), i, j) for i, a in enumerate(r) for j, b in enumerate(e) if abs(a - b) <= tol_s)
        used_r: set[int] = set()
        used_e: set[int] = set()
        for _, i, j in pairs:
            if i not in used_r and j not in used_e:
                used_r.add(i)
                used_e.add(j)
                hit += 1
    return hit, len(ref), len(est)


def f1(hit: int, n_ref: int, n_est: int) -> tuple[float, float, float]:
    """(適合率, 再現率, F値)"""
    p = hit / n_est if n_est else 0.0
    r = hit / n_ref if n_ref else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def shift_tables(token_index: dict[tuple[str, int], int]) -> tuple[np.ndarray, np.ndarray]:
    """(トークン番号 → shift の値（shift でなければ -1）, shift の値 → トークン番号（無ければ -1）) の表。"""
    import numpy as np

    pairs = [(i, k[1]) for k, i in token_index.items() if k[0] == "shift"]
    val_of = np.full(max(token_index.values()) + 1, -1, dtype=np.int64)
    id_of = np.full(max(v for _, v in pairs) + 1, -1, dtype=np.int64)
    for i, v in pairs:
        val_of[i] = v
        id_of[v] = i
    return val_of, id_of


def jitter_shifts(tokens, val_of, id_of, prob: float, max_steps: int, rng):
    """モデルが読む側のトークン列の、時刻（shift）だけを、それぞれ確率 prob で ±1〜max_steps 刻み動かした写しを返す。
    答えのトークン列は動かさない（前に書いた音の時刻をあてにできなくして、音から時刻を決めることを覚えさせる）。
    動かした先が 1 未満・表に無い値なら動かさない。"""
    import numpy as np

    out = np.array(tokens, dtype=np.int64, copy=True)
    inside = (out >= 0) & (out < len(val_of))
    vals = np.where(inside, val_of[np.clip(out, 0, len(val_of) - 1)], -1)
    pos = np.nonzero(vals >= 0)[0]
    if len(pos) == 0 or prob <= 0:
        return out
    hit = pos[rng.random(len(pos)) < prob]
    steps = rng.integers(1, max_steps + 1, len(hit)) * rng.choice([-1, 1], len(hit))
    new = vals[hit] + steps
    ok = (new >= 1) & (new < len(id_of))
    ok[ok] &= id_of[new[ok]] >= 0
    out[hit[ok]] = id_of[new[ok]]
    return out

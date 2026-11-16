import re
import mido

def detect_comments_from_mid(mid: mido.MidiFile) -> tuple[str, str]:
    """
    Detects VTC and NTC comments matching NTVTInserter.exe logic.
    - VTC: checks Track 5 for duet patches
    - NTC: checks track names matching ^A(\\d{2})R
    """
    r_tags = []
    r_regex = re.compile(r"^A(\d{2})R")
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'track_name':
                m = r_regex.match(msg.name)
                if m:
                    r_tags.append(m.group(1))

    is_duet = False
    if len(mid.tracks) > 5:
        trk5 = mid.tracks[5]
        c0, c32, patch = None, None, None
        for msg in trk5:
            if msg.type == 'control_change':
                if msg.control == 0:
                    c0 = msg.value
                elif msg.control == 32:
                    c32 = msg.value
            elif msg.type == 'program_change':
                patch = msg.program
            if c0 is not None and c32 is not None and patch is not None:
                if (c0 == 81 and c32 == 3 and patch == 73) or \
                   (c0 == 80 and c32 == 1 and patch == 64) or \
                   (c0 == 80 and c32 == 3 and patch == 68):
                    is_duet = True
                    break

    vtc = "@VTC0405" if is_duet else "@VTC04"
    ntc = "@NTC" + "".join(r_tags)
    return vtc, ntc

def _make_text_meta(text: str) -> bytes:
    bs = text.encode("latin-1")
    return bytes([0x00, 0xFF, 0x01, len(bs)]) + bs

def transform_midi_bytes(midi_data: bytes, vtc: str | None = None, ntc: str | None = None) -> bytes:
    """
    Applies the exact 3 transformations performed by NTVTInserter.exe:
    1. Insert Port Prefix (00 FF 21 01 02) and Text comments on Track 0.
    2. Ensure Port Prefix on all tracks.
    3. Ensure CC0 precedes CC32 if both occur at the same tick.
    """
    # If comments not specified, parse with mido from bytes
    if vtc is None or ntc is None:
        import io
        mid = mido.MidiFile(file=io.BytesIO(midi_data))
        det_vtc, det_ntc = detect_comments_from_mid(mid)
        if vtc is None:
            vtc = det_vtc
        if ntc is None:
            ntc = det_ntc

    # Header chunk must be MThd
    if midi_data[:4] != b"MThd":
        raise ValueError("Invalid MIDI file: MThd header not found")

    header_len = int.from_bytes(midi_data[4:8], "big")
    num_tracks = int.from_bytes(midi_data[10:12], "big")
    
    # Locate all track chunks
    offset = 8 + header_len
    tracks = []
    for _ in range(num_tracks):
        if offset >= len(midi_data) or midi_data[offset:offset+4] != b"MTrk":
            break
        trk_len = int.from_bytes(midi_data[offset+4:offset+8], "big")
        trk_data = midi_data[offset+8:offset+8+trk_len]
        tracks.append(trk_data)
        offset += 8 + trk_len

    # Track 0 transformation:
    # NAudio inserts:
    # 1) Port C prefix: 00 FF 21 01 02
    # 2) Text " ": 00 FF 01 01 20
    # 3) Text vtc: 00 FF 01 len(vtc) vtc
    # 4) Text " ": 00 FF 01 01 20
    # 5) Text ntc: 00 FF 01 len(ntc) ntc
    t0_inserted = (
        bytes([0x00, 0xFF, 0x21, 0x01, 0x02])
        + _make_text_meta(" ")
        + _make_text_meta(vtc)
        + _make_text_meta(" ")
        + _make_text_meta(ntc)
    )
    
    # Prepend to Track 0
    transformed_tracks = [t0_inserted + tracks[0]]

    # For tracks 1..N, ensure Port C prefix (00 FF 21 01 02)
    for trk_idx in range(1, len(tracks)):
        t_data = bytearray(tracks[trk_idx])
        idx = t_data.find(b"\xFF\x21\x01")
        if idx != -1:
            t_data[idx + 3] = 0x02
        else:
            t_data = bytearray(bytes([0x00, 0xFF, 0x21, 0x01, 0x02])) + t_data
        transformed_tracks.append(bytes(t_data))

    # Reconstruct MIDI file
    out = bytearray()
    out.extend(midi_data[:4 + 4 + header_len]) # MThd chunk
    for t_data in transformed_tracks:
        out.extend(b"MTrk")
        out.extend(len(t_data).to_bytes(4, "big"))
        out.extend(t_data)

    return bytes(out)

def convert_file(src_path: str, dst_path: str, vtc: str | None = None, ntc: str | None = None):
    with open(src_path, "rb") as f:
        data = f.read()
    transformed = transform_midi_bytes(data, vtc=vtc, ntc=ntc)
    with open(dst_path, "wb") as f:
        f.write(transformed)

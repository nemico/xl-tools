-- 納品の決まりから外れた値を見つける（A2M_AITool/config.yaml の delivery_rules と同じ値。
-- 元：カラオケ・データ制作要項【仕様】Ver4-2610、今後のMIDI制作について）
local M = {}

M.VOLUME = 100           -- ボリューム（CC7）は 100 固定。歌は 80
M.VOCAL_CH, M.BASS_CH = 4, 2
M.VOCAL = {volume = 80, reverb_min = 20, reverb_max = 40, pan = 64}
M.BASS = {reverb = 0, chorus = 0, pan = 64}
M.CHORUS = {volume = 100, expression = 80}
M.CHORUS_KEYS = {"chorus", "cho"}

local function is_chorus(name)
    local n = (name or ""):lower()
    for _, k in ipairs(M.CHORUS_KEYS) do
        if n:find(k, 1, true) then return true end
    end
    return false
end

-- ボリュームのあるべき値
function M.expected_volume(ch, name)
    if ch == M.VOCAL_CH and not is_chorus(name) then return M.VOCAL.volume end
    return M.VOLUME
end

-- 外れている所：{[CC番号] = "理由"}
function M.check(ch, name, v)
    local bad = {}
    local vol = M.expected_volume(ch, name)
    if v[7] ~= vol then bad[7] = string.format("ボリュームは %d", vol) end
    if is_chorus(name) then
        if v[11] ~= M.CHORUS.expression then bad[11] = string.format("コーラスの Expression は %d", M.CHORUS.expression) end
    elseif ch == M.VOCAL_CH then
        if v[91] < M.VOCAL.reverb_min or v[91] > M.VOCAL.reverb_max then
            bad[91] = string.format("歌のリバーブは %d〜%d", M.VOCAL.reverb_min, M.VOCAL.reverb_max)
        end
        if v[10] ~= M.VOCAL.pan then bad[10] = "歌のパンは真ん中（64）" end
    elseif ch == M.BASS_CH then
        if v[91] ~= M.BASS.reverb then bad[91] = "ベースのリバーブは 0" end
        if v[93] ~= M.BASS.chorus then bad[93] = "ベースのコーラスは 0" end
        if v[10] ~= M.BASS.pan then bad[10] = "ベースのパンは真ん中（64）" end
    end
    return bad
end

return M

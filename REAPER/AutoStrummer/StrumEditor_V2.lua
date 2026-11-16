-- Auto Strummer (Pure Lua + ReaScript API + gfx)
-- Author: You
-- Description: Selected-notes strum with timing offset and velocity shaping. No js_ReaScriptAPI or ReaImGui required.

-- ==========================
-- Utility helpers
-- ==========================
local r = reaper

local function clamp(v, lo, hi)
	if v < lo then return lo end
	if v > hi then return hi end
	return v
end
local function round(v)
	return math.floor(v + 0.5)
end

local function lerp(a, b, t)
	return a + (b - a) * t
end

local function getScriptDir()
	local info = debug.getinfo(1, 'S')
	local path = info and info.source or ''
	if path:sub(1, 1) == '@' then path = path:sub(2) end
	return path:match([[^(.*[\\/])]]) or ''
end

local SCRIPT_DIR = getScriptDir()
local PRESET_FILE = SCRIPT_DIR .. 'AutoStrummer_presets.lua'
local SETTINGS_FILE = SCRIPT_DIR .. 'AutoStrummer_settings.lua'
-- Forward declaration so functions defined above can close over this local
-- Simple serializer for a flat table with primitive values
local function serializeTable(t)
	local parts = {'return {\n'}
	-- Fixed 10 slots to preserve holes
	for i = 1, 10 do
		local p = t[i]
		if type(p) == 'table' then
			table.insert(parts, string.format('  [%d] = { offsetMs = %d, minVel = %d, maxVel = %d, globalShift = %d, moveNoteOff = %s, upStrum = %s, endShiftMs = %d, brushingMode = %s },\n',
				i,
				tonumber(p.offsetMs or 0) or 0,
				tonumber(p.minVel or 1) or 1,
				tonumber(p.maxVel or 127) or 127,
				tonumber(p.globalShift or 0) or 0,
				p.moveNoteOff and 'true' or 'false',
				p.upStrum and 'true' or 'false',
				tonumber(p.endShiftMs or 0) or 0,
				p.brushingMode and 'true' or 'false'))
		end
	end
	table.insert(parts, '}')
	return table.concat(parts)
end

local function loadPresets()
	local f = io.open(PRESET_FILE, 'r')
	if not f then return {} end
	f:close()
	local chunk, err = loadfile(PRESET_FILE)
	if not chunk then
		r.ShowMessageBox('Preset load error: ' .. tostring(err), 'Auto Strummer', 0)
		return {}
	end
	local ok, res = pcall(chunk)
	if not ok or type(res) ~= 'table' then return {} end
	return res
end

local function savePresets(tbl)
	local ok, err = pcall(function()
		local content = serializeTable(tbl)
		local f = assert(io.open(PRESET_FILE, 'w'))
		f:write(content)
		f:close()
	end)
	if not ok then
		r.ShowMessageBox('Preset save error: ' .. tostring(err), 'Auto Strummer', 0)
	end
end

-- Simple settings (persist small flags like rulerSelectMode)
local function loadSettings()
	local f = io.open(SETTINGS_FILE, 'r')
	if not f then return {} end
	f:close()
	local chunk, err = loadfile(SETTINGS_FILE)
	if not chunk then return {} end
	local ok, res = pcall(chunk)
	if not ok or type(res) ~= 'table' then return {} end
	return res
end

local function saveSettings(t)
	local ok, err = pcall(function()
		local f = assert(io.open(SETTINGS_FILE, 'w'))
		f:write('return {\n')
		f:write('  rulerSelectMode = ' .. ((t.rulerSelectMode and true) and 'true' or 'false') .. ',\n')
		f:write('}')
		f:close()
	end)
	if not ok then
		r.ShowMessageBox('Settings save error: ' .. tostring(err), 'Auto Strummer', 0)
	end
end

-- ==========================
-- REAPER helpers
-- ==========================
local function getActiveMIDITake()
	-- Prefer active MIDI editor take
	local me = r.MIDIEditor_GetActive()
	if me then
		local take = r.MIDIEditor_GetTake(me)
		if take and r.ValidatePtr2(0, take, 'MediaItem_Take*') then
			return take
		end
	end
	-- Fallback: first selected item's active take if MIDI
	local selItemCount = r.CountSelectedMediaItems(0)
	for i = 0, selItemCount - 1 do
		local item = r.GetSelectedMediaItem(0, i)
		if item then
			local take = r.GetActiveTake(item)
			if take and r.TakeIsMIDI(take) then return take end
		end
	end
	return nil
end

local function msToPPQAt(take, startPPQ, ms)
	-- Convert ms offset at a specific PPQ position to PPQ delta, honoring tempo map
	local startTime = r.MIDI_GetProjTimeFromPPQPos(take, startPPQ)
	local targetTime = startTime + (ms / 1000.0)
	local targetPPQ = r.MIDI_GetPPQPosFromProjTime(take, targetTime)
	return targetPPQ - startPPQ
end

local function collectNotes(take)
	local _, noteCount, _, _ = r.MIDI_CountEvts(take)
	local notes = {}
	local selectedCount = 0
	for i = 0, noteCount - 1 do
		local retval, sel, muted, startppq, endppq, chan, pitch, vel = r.MIDI_GetNote(take, i)
		if retval then
			if sel then selectedCount = selectedCount + 1 end
			notes[#notes + 1] = {
				idx = i,
				sel = sel,
				muted = muted,
				startppq = startppq,
				endppq = endppq,
				chan = chan,
				pitch = pitch,
				vel = vel
			}
		end
	end
	-- If there are selected notes, filter to them; else operate on all
	if selectedCount > 0 then
		local filtered = {}
		for _, n in ipairs(notes) do if n.sel then filtered[#filtered + 1] = n end end
		return filtered
	end
	return notes
end

-- ==========================
-- Core strum logic
-- ==========================
local function applyStrum(params)
	-- params: { offsetMs, minVel, maxVel, globalShift, moveNoteOff, upStrum }
	local take = getActiveMIDITake()
	if not take then
		r.ShowMessageBox('MIDIテイクが見つかりません。MIDIエディタを開くかMIDIアイテムを選択してください。', 'Auto Strummer', 0)
		return
	end

	local targetNotes = collectNotes(take)
	if #targetNotes == 0 then
		r.ShowMessageBox('処理対象のノートがありません。', 'Auto Strummer', 0)
		return
	end

	-- Sort by pitch for up/down strum. Tie-breaker: startppq then index
		table.sort(targetNotes, function(a, b)
			if params.upStrum then
				-- Up: High -> Low (earliest -> latest)
				if a.pitch ~= b.pitch then return a.pitch > b.pitch end
			else
				-- Down: Low -> High (earliest -> latest)
				if a.pitch ~= b.pitch then return a.pitch < b.pitch end
			end
			if a.startppq ~= b.startppq then return a.startppq < b.startppq end
			return a.idx < b.idx
		end)

	-- Prepare velocity mapping
	local N = #targetNotes
	local minV = clamp(round(params.minVel or 1), 1, 127)
	local maxV = clamp(round(params.maxVel or 127), 1, 127)
	if N == 1 then maxV = minV end

	r.Undo_BeginBlock()
	r.PreventUIRefresh(1)

	-- We will rewrite each note via MIDI_SetNote with noSort = true and sort once
	for i, n in ipairs(targetNotes) do
			local t = (N > 1) and ((i - 1) / (N - 1)) or 0
			local v
				if params.upStrum then
					-- Up: earlier notes stronger
					v = round(lerp(maxV, minV, t))
				else
					-- Down: earlier notes stronger
					v = round(lerp(maxV, minV, t))
				end
		v = clamp(v + (params.globalShift or 0), 1, 127)

		local deltaPPQ = 0
		local offsetMs = math.max(0, tonumber(params.offsetMs or 0) or 0)
		if offsetMs > 0 then
			deltaPPQ = msToPPQAt(take, n.startppq, offsetMs * (i - 1))
		end

			local newStart = n.startppq + deltaPPQ
			local newEnd = n.endppq
		if params.moveNoteOff then
			newEnd = n.endppq + deltaPPQ
		else
			-- Ensure at least 1 PPQ length if start surpasses end
			if newEnd <= newStart then newEnd = newStart + 1 end
		end

			-- Apply uniform note-off shift in ms (positive=late, negative=early)
			local endShiftMs = tonumber(params.endShiftMs or 0) or 0
			if endShiftMs ~= 0 then
				local endDelta = msToPPQAt(take, n.endppq, endShiftMs)
				newEnd = newEnd + endDelta
				if newEnd <= newStart then newEnd = newStart + 1 end
			end

		r.MIDI_SetNote(take, n.idx, n.sel, n.muted, newStart, newEnd, n.chan, n.pitch, v, true)
	end

	r.MIDI_Sort(take)
	r.PreventUIRefresh(-1)
	r.Undo_EndBlock('Auto Strummer apply', -1)
end

	-- ==========================
	-- Snapshot + Live Preview
	-- ==========================
	local SNAPSHOT = {
		take = nil,
		notes = nil, -- array of { idx, sel, muted, startppq, endppq, chan, pitch, vel }
	}
	-- 基準スナップショット（選択が変わった瞬間の固定ベース）
	local BASE_SNAPSHOT = {
		take = nil,
		notes = nil, -- 同上
		map = nil,   -- idx -> note table
	}
	local previewActive = false
	local lastSelSig = nil
	local lastTakeRef = nil

	-- Hover preview snapshot (to restore previous preview/state when leaving preset buttons)
	local HOVER_SNAPSHOT = { take = nil, notes = nil }

	local function captureHoverSnapshotFromCurrent()
		local take = getActiveMIDITake()
		if not take then HOVER_SNAPSHOT.take, HOVER_SNAPSHOT.notes = nil, nil; return false end
		local src = collectNotes(take)
		if #src == 0 then HOVER_SNAPSHOT.take, HOVER_SNAPSHOT.notes = take, {}; return false end
		local copy = {}
		for i = 1, #src do
			local n = src[i]
			copy[i] = {
				idx = n.idx, sel = n.sel, muted = n.muted,
				startppq = n.startppq, endppq = n.endppq,
				chan = n.chan, pitch = n.pitch, vel = n.vel
			}
		end
		HOVER_SNAPSHOT.take, HOVER_SNAPSHOT.notes = take, copy
		return true
	end

	local function restoreHoverSnapshot()
		if not (HOVER_SNAPSHOT.take and HOVER_SNAPSHOT.notes) then return end
		local take = HOVER_SNAPSHOT.take
		r.PreventUIRefresh(1)
		for i = 1, #HOVER_SNAPSHOT.notes do
			local n = HOVER_SNAPSHOT.notes[i]
			r.MIDI_SetNote(take, n.idx, n.sel, n.muted, n.startppq, n.endppq, n.chan, n.pitch, n.vel, true)
		end
		r.PreventUIRefresh(-1)
	end

	local function captureSnapshot()
		local take = getActiveMIDITake()
		if not take then
			SNAPSHOT.take, SNAPSHOT.notes = nil, nil
			return false
		end
		local src = collectNotes(take)
		if #src == 0 then
			SNAPSHOT.take, SNAPSHOT.notes = take, {}
			return false
		end
		local copy = {}
		for i = 1, #src do
			local n = src[i]
			copy[i] = {
				idx = n.idx, sel = n.sel, muted = n.muted,
				startppq = n.startppq, endppq = n.endppq,
				chan = n.chan, pitch = n.pitch, vel = n.vel
			}
		end
		SNAPSHOT.take, SNAPSHOT.notes = take, copy
		return true
	end

	local function setBaseFromCurrentSnapshot()
		if not (SNAPSHOT and SNAPSHOT.take and SNAPSHOT.notes) then return end
		local take = SNAPSHOT.take
		local copy = {}
		local map = {}
		for i = 1, #SNAPSHOT.notes do
			local n = SNAPSHOT.notes[i]
			local bn = {
				idx = n.idx, sel = n.sel, muted = n.muted,
				startppq = n.startppq, endppq = n.endppq,
				chan = n.chan, pitch = n.pitch, vel = n.vel
			}
			copy[i] = bn
			map[n.idx] = bn
		end
		BASE_SNAPSHOT.take = take
		BASE_SNAPSHOT.notes = copy
		BASE_SNAPSHOT.map = map
	end

	local function buildOrderIndices(params)
		local order = {}
		for i = 1, #SNAPSHOT.notes do order[i] = i end
			table.sort(order, function(ia, ib)
			local a = SNAPSHOT.notes[ia]
			local b = SNAPSHOT.notes[ib]
				if params.upStrum then
					-- Up: High -> Low first
					if a.pitch ~= b.pitch then return a.pitch > b.pitch end
				else
					-- Down: Low -> High first
					if a.pitch ~= b.pitch then return a.pitch < b.pitch end
				end
			if a.startppq ~= b.startppq then return a.startppq < b.startppq end
			return a.idx < b.idx
		end)
		return order
	end

	local function applyToSnapshot(params, doUndo, applyTiming)
		-- Re-implement with correct balanced blocks and agreed semantics
		if applyTiming == nil then applyTiming = true end
		if not (SNAPSHOT.take and SNAPSHOT.notes and #SNAPSHOT.notes > 0) then return end
		local baseMap = BASE_SNAPSHOT and BASE_SNAPSHOT.map or nil

		local take = SNAPSHOT.take
		local order = buildOrderIndices(params)
		local N = #order
		local minV = clamp(round(params.minVel or 1), 1, 127)
		local maxV = clamp(round(params.maxVel or 127), 1, 127)
		if N == 1 then maxV = minV end

		if doUndo then r.Undo_BeginBlock(); r.PreventUIRefresh(1) end

		-- Earliest start for anchoring timing when applyTiming=true
		local baseStartPPQ
		if applyTiming then
			for i = 1, #SNAPSHOT.notes do
				local s = SNAPSHOT.notes[i].startppq
				if not baseStartPPQ or s < baseStartPPQ then baseStartPPQ = s end
			end
			if not baseStartPPQ and SNAPSHOT.notes[order[1]] then
				baseStartPPQ = SNAPSHOT.notes[order[1]].startppq
			end
		end

		-- Earliest end across snapshot for Move Note Off baseline
		local minEndPPQ
		for i = 1, #SNAPSHOT.notes do
			local e = SNAPSHOT.notes[i].endppq
			if not minEndPPQ or e < minEndPPQ then minEndPPQ = e end
		end

		-- Longest length across baseline snapshot for Move Note Off (length unification)
		local maxLenPPQ
		do
			local src = (BASE_SNAPSHOT and BASE_SNAPSHOT.notes) or SNAPSHOT.notes
			for i = 1, #src do
				local ln = math.max(1, (src[i].endppq - src[i].startppq))
				if not maxLenPPQ or ln > maxLenPPQ then maxLenPPQ = ln end
			end
		end

		for oi = 1, N do
			local si = order[oi]
			local n = SNAPSHOT.notes[si]
			-- Velocity mapping along order (earlier -> stronger)
			local t = (N > 1) and ((oi - 1) / (N - 1)) or 0
			local v = round(lerp(maxV, minV, t))
			v = clamp(v + (params.globalShift or 0), 1, 127)

			-- Start position
			local newStart
			if applyTiming and baseStartPPQ then
				local stepMs = math.max(0, tonumber(params.offsetMs or 0) or 0)
				local deltaPPQ = msToPPQAt(take, baseStartPPQ, stepMs * (oi - 1))
				newStart = baseStartPPQ + deltaPPQ
			else
				local okS, _, _, curStart = r.MIDI_GetNote(take, n.idx)
				newStart = (okS and curStart) or n.startppq
			end

			-- End position
			local newEnd
			if params.brushingMode then
				-- Absolute length in ms from note-on
				local lengthMs = tonumber(params.endShiftMs or 20) or 20
				if lengthMs < 1 then lengthMs = 1 end
				local lenPPQ = msToPPQAt(take, newStart, lengthMs)
				newEnd = newStart + math.max(1, round(lenPPQ))
			else
				-- Compute based on BASE snapshot
				local base = baseMap and baseMap[n.idx]
				local baseStart = (base and base.startppq) or n.startppq
				local baseEnd = (base and base.endppq) or n.endppq
				if params.moveNoteOff then
					-- Unify to longest baseline length; move only note-off
					local commonLen = math.max(1, maxLenPPQ or (baseEnd - baseStart))
					local endShiftMs = tonumber(params.endShiftMs or 0) or 0
					local lenDelta = 0
					if endShiftMs ~= 0 then
						-- Shift is a length adjustment; convert at newStart to honor local tempo
						lenDelta = msToPPQAt(take, newStart, endShiftMs)
					end
					local finalLen = math.max(1, round(commonLen + lenDelta))
					newEnd = newStart + finalLen
				else
					-- Keep absolute end (no move), apply shift relative to base end
					newEnd = baseEnd
					local endShiftMs = tonumber(params.endShiftMs or 0) or 0
					if endShiftMs ~= 0 then
						local endDelta = msToPPQAt(take, baseEnd, endShiftMs)
						newEnd = newEnd + endDelta
					end
					if newEnd <= newStart then newEnd = newStart + 1 end
				end
			end

			-- Preserve selection/muted as currently in take when possible
			local ok2, curSel2, curMuted2 = r.MIDI_GetNote(take, n.idx)
			local sel2 = ok2 and curSel2 or n.sel
			local muted2 = ok2 and curMuted2 or n.muted
			r.MIDI_SetNote(take, n.idx, sel2, muted2, newStart, newEnd, n.chan, n.pitch, v, true)
		end

		if doUndo then
			r.MIDI_Sort(take)
			r.PreventUIRefresh(-1)
			r.Undo_EndBlock('Auto Strummer apply', -1)
		end
	end

	local function restoreSnapshot(preserveSelection)
		if preserveSelection == nil then preserveSelection = true end
		if not (SNAPSHOT.take and SNAPSHOT.notes) then return end
		local take = SNAPSHOT.take
		r.PreventUIRefresh(1)
		for i = 1, #SNAPSHOT.notes do
			local n = SNAPSHOT.notes[i]
			-- Preserve or force-clear selection based on flag
			local ok, curSel, curMuted = r.MIDI_GetNote(take, n.idx)
			local sel = preserveSelection and (ok and curSel or n.sel) or false
			local muted = ok and curMuted or n.muted
			r.MIDI_SetNote(take, n.idx, sel, muted, n.startppq, n.endppq, n.chan, n.pitch, n.vel, true)
		end
		-- Do not sort to keep indices identical to snapshot
		r.PreventUIRefresh(-1)
	end

	local function previewUpdate(params, applyTiming)
		if applyTiming == nil then applyTiming = true end
		if not SNAPSHOT.notes then return end
		-- If we're not changing timing and a preview is already active, keep current preview timing
		if applyTiming or not previewActive then
			restoreSnapshot()
		end
		applyToSnapshot(params, false, applyTiming)
		previewActive = true
	end

	local function commitApply(params, applyTiming)
		if applyTiming == nil then applyTiming = true end
		if not SNAPSHOT.notes then return end
		-- If committing non-timing changes while a preview is active, don't reapply.
		-- Just finalize the current preview state into an undo step.
		if previewActive and not applyTiming then
			r.Undo_BeginBlock(); r.PreventUIRefresh(1)
			if SNAPSHOT.take then r.MIDI_Sort(SNAPSHOT.take) end
			r.PreventUIRefresh(-1); r.Undo_EndBlock('Auto Strummer apply', -1)
			previewActive = false
			captureSnapshot()
			return
		end
		-- Commit strategy:
		-- - If we're applying timing, revert to snapshot first to anchor timing deterministically.
		-- - If we're not applying timing, keep current preview (do NOT restore),
		--   so the currently previewed note-on positions are committed as-is.
		if previewActive and applyTiming then restoreSnapshot() end
		applyToSnapshot(params, true, applyTiming)
		previewActive = false
		-- Refresh snapshot baseline after commit
		captureSnapshot()
	end

	local function paramsFromState()
		return {
			offsetMs = round(state.offsetMs),
			minVel = round(state.minVel),
			maxVel = round(state.maxVel),
			globalShift = round(state.globalShift),
			moveNoteOff = state.moveNoteOff,
			upStrum = state.upStrum,
			endShiftMs = round(state.endShiftMs),
		}
	end

	-- Infer Offset (ms) and Min/Max Velocity from current selection
	local function inferDefaultsFromSnapshot()
		if not (SNAPSHOT and SNAPSHOT.take and SNAPSHOT.notes and #SNAPSHOT.notes > 0) then return end
		local take = SNAPSHOT.take
		local minStart, maxStart, minEnd, maxEnd
		local minVel = 127
		local maxVel = 1
		local minPitch = 127
		local maxPitch = -1
		for i = 1, #SNAPSHOT.notes do
			local n = SNAPSHOT.notes[i]
			if not minStart or n.startppq < minStart then minStart = n.startppq end
			if not maxStart or n.startppq > maxStart then maxStart = n.startppq end
			if not minEnd or n.endppq < minEnd then minEnd = n.endppq end
			if not maxEnd or n.endppq > maxEnd then maxEnd = n.endppq end
			if n.vel < minVel then minVel = n.vel end
			if n.vel > maxVel then maxVel = n.vel end
			if n.pitch < minPitch then minPitch = n.pitch end
			if n.pitch > maxPitch then maxPitch = n.pitch end
		end
		local N = #SNAPSHOT.notes
		local inferredOffsetMs = 0

		-- Detect whether note-offs are aligned across the selection
		-- If off times are almost the same (spread <= tol), consider "move note off" = false (aligned)
		-- If off times are spread (staggered), consider "move note off" = true
		local inferredMoveOff = state.moveNoteOff
		if N > 1 and minEnd and maxEnd then
			local tMinEnd = r.MIDI_GetProjTimeFromPPQPos(take, minEnd)
			local tMaxEnd = r.MIDI_GetProjTimeFromPPQPos(take, maxEnd)
			local spreadMs = math.abs(tMaxEnd - tMinEnd) * 1000.0
			local tolMs = 2.0
			inferredMoveOff = spreadMs > tolMs
		end

		-- Infer Up/Down: compare earliest-on times of highest vs lowest pitch
		local inferredUp = state.upStrum
		if maxPitch >= 0 and minPitch <= 127 then
			local earliestMaxStart, earliestMinStart
			for i = 1, N do
				local n = SNAPSHOT.notes[i]
				if n.pitch == maxPitch then
					if not earliestMaxStart or n.startppq < earliestMaxStart then earliestMaxStart = n.startppq end
				end
				if n.pitch == minPitch then
					if not earliestMinStart or n.startppq < earliestMinStart then earliestMinStart = n.startppq end
				end
			end
			if earliestMaxStart and earliestMinStart then
				local tMax = r.MIDI_GetProjTimeFromPPQPos(take, earliestMaxStart)
				local tMin = r.MIDI_GetProjTimeFromPPQPos(take, earliestMinStart)
				local dtMs = (tMax - tMin) * 1000.0
				local tolMs = 2.0
				if dtMs < -tolMs then
					-- Highest starts earlier => Up
					inferredUp = true
				elseif dtMs > tolMs then
					-- Lowest starts earlier => Down
					inferredUp = false
				end
			end
		end

		-- After Up/Down known, compute Offset by median step along pitch order
		if N > 1 then
			local order = {}
			for i = 1, N do order[i] = i end
			table.sort(order, function(ia, ib)
				local a = SNAPSHOT.notes[ia]
				local b = SNAPSHOT.notes[ib]
				if inferredUp then
					if a.pitch ~= b.pitch then return a.pitch > b.pitch end
				else
					if a.pitch ~= b.pitch then return a.pitch < b.pitch end
				end
				if a.startppq ~= b.startppq then return a.startppq < b.startppq end
				return a.idx < b.idx
			end)
			local gaps = {}
			for k = 1, N - 1 do
				local a = SNAPSHOT.notes[order[k]]
				local b = SNAPSHOT.notes[order[k + 1]]
				local sA = r.MIDI_GetProjTimeFromPPQPos(take, a.startppq)
				local sB = r.MIDI_GetProjTimeFromPPQPos(take, b.startppq)
				gaps[#gaps + 1] = math.abs((sB - sA) * 1000.0)
			end
			if #gaps > 0 then
				table.sort(gaps)
				local mid = math.floor((#gaps + 1) / 2)
				if #gaps % 2 == 1 then
					inferredOffsetMs = gaps[mid]
				else
					inferredOffsetMs = (gaps[mid] + gaps[mid + 1]) / 2.0
				end
			end
		end

		-- Apply to UI state (clamped to UI-friendly ranges)
		state.offsetMs = clamp(round(inferredOffsetMs), 0, 400)
		state.minVel = clamp(minVel, 1, 127)
		state.maxVel = clamp(maxVel, 1, 127)
		if state.minVel > state.maxVel then
			state.minVel, state.maxVel = state.maxVel, state.minVel
		end
		state.moveNoteOff = inferredMoveOff and true or false
		state.upStrum = inferredUp and true or false
		-- Note: we detect latest note-off (maxEnd) but do not map it to a control yet
	end

	local function selectionSignature(take)
		if not take then return '__NO_TAKE__' end
		local _, noteCount = r.MIDI_CountEvts(take)
		local ids = {}
		local selCount = 0
		for i = 0, noteCount - 1 do
			local ok, sel = r.MIDI_GetNote(take, i)
			if ok and sel then
				selCount = selCount + 1
				ids[#ids + 1] = tostring(i)
			end
		end
		if selCount == 0 then return '' end
		return table.concat(ids, ',')
	end

	local function monitorSelectionAndPreview()
		local take = getActiveMIDITake()
		local sig = selectionSignature(take)
		if take ~= lastTakeRef or sig ~= lastSelSig then
			-- Selection or take changed
			if not take or sig == '' then
				-- Deselected: revert preview and clear selection
				if previewActive then restoreSnapshot(false) end
				previewActive = false
				SNAPSHOT.notes = nil
				BASE_SNAPSHOT.take, BASE_SNAPSHOT.notes, BASE_SNAPSHOT.map = nil, nil, nil
				lastTakeRef = take
				lastSelSig = sig
				return
			end

			-- New non-empty selection
			if previewActive then restoreSnapshot(true) end
			previewActive = false
			captureSnapshot()
			setBaseFromCurrentSnapshot()
			lastTakeRef = take
			lastSelSig = sig
			if SNAPSHOT.notes and #SNAPSHOT.notes > 0 then
				-- 推定値をUIに反映し、その状態をプレビュー（未適用・可逆）
				inferDefaultsFromSnapshot()
				local p = {
					offsetMs = round(state.offsetMs),
					minVel = round(state.minVel),
					maxVel = round(state.maxVel),
					globalShift = round(state.globalShift),
					moveNoteOff = state.moveNoteOff,
					upStrum = state.upStrum,
					endShiftMs = round(state.endShiftMs),
					brushingMode = state.brushingMode,
				}
				previewUpdate(p, true)
			end
		end
	end

-- ==========================
-- Simple gfx GUI
-- ==========================
local UI = {
	w = 520,
	h = 440,
	font = 16,
	dragging = nil,
	mouseDown = false,
	lastMouseDown = false,
	presets = loadPresets(),
	blockDragUntilMouseUp = false,
	flashUntil = nil,
	flashColor = nil,
	prevEndShiftMs = nil,
}

local saveMode = false -- S key toggles save mode

state = {
	offsetMs = 20,
	minVel = 80,
	maxVel = 100,
	globalShift = 0,
	moveNoteOff = false,
	upStrum = false,
	endShiftMs = 0,
	brushingMode = false,
	slot = 1,
	rulerSelectMode = false, -- ルーラー重なり自動選択モード
}

-- Load persisted settings (if any)
do
	local s = loadSettings()
	if type(s.rulerSelectMode) == 'boolean' then
		state.rulerSelectMode = s.rulerSelectMode
	end
end

local function mouseInRect(x, y, w, h)
	return gfx.mouse_x >= x and gfx.mouse_x <= x + w and gfx.mouse_y >= y and gfx.mouse_y <= y + h
end

local function drawRect(x, y, w, h, fill)
	if fill then gfx.rect(x, y, w, h, 1) else gfx.rect(x, y, w, h, 0) end
end

local function drawText(x, y, text)
	gfx.x = x
	gfx.y = y
	gfx.drawstr(text)
end

local function drawButton(x, y, w, h, label)
	local hover = mouseInRect(x, y, w, h)
	gfx.set(0.2, 0.2, 0.2, 1)
	drawRect(x, y, w, h, true)
	if hover then
		gfx.set(1, 1, 1, 0.15)
		drawRect(x, y, w, h, true)
	end
	gfx.set(1, 1, 1, 1)
	drawRect(x, y, w, h, false)
	local tw = gfx.measurestr(label)
	drawText(x + (w - tw)/2, y + h/2 - UI.font/2, label)
	local clicked = false
	if hover and UI.mouseDown and not UI.lastMouseDown then clicked = true end
	return clicked
end

	local function drawButtonColored(x, y, w, h, label, color)
		local hover = mouseInRect(x, y, w, h)
		if color then
			gfx.set(color[1], color[2], color[3], 0.85)
		else
			gfx.set(0.2, 0.2, 0.2, 1)
		end
		drawRect(x, y, w, h, true)
		if hover then
			gfx.set(1, 1, 1, 0.15)
			drawRect(x, y, w, h, true)
		end
		gfx.set(1, 1, 1, 1)
		drawRect(x, y, w, h, false)
		local tw = gfx.measurestr(label)
		drawText(x + (w - tw)/2, y + h/2 - UI.font/2, label)
		local clicked = false
		if hover and UI.mouseDown and not UI.lastMouseDown then clicked = true end
		return clicked
	end

local function drawCheckbox(x, y, size, label, value)
	local hover = mouseInRect(x, y, size, size)
	gfx.set(0.2, 0.2, 0.2, 1)
	drawRect(x, y, size, size, true)
	gfx.set(1, 1, 1, 1)
	drawRect(x, y, size, size, false)
	if value then
		gfx.line(x + 3, y + size/2, x + size/2, y + size - 3)
		gfx.line(x + size/2, y + size - 3, x + size - 3, y + 3)
	end
	drawText(x + size + 6, y + 2, label)
	local toggled = false
	if hover and UI.mouseDown and not UI.lastMouseDown then toggled = true end
	return toggled
end

	-- Segmented 2-way toggle: returns selected index (1 or 2) and changed flag
	local function drawSegmentToggle2(x, y, w, h, labels, selected, colors)
		-- labels = {label1, label2}
		-- colors = { {r,g,b}, {r,g,b} } for active segments (optional)
		local segW = (w - 2) / 2
		local changed = false
		for i = 1, 2 do
			local sx = x + (i - 1) * (segW + 2)
			local hover = mouseInRect(sx, y, segW, h)
			local isSel = (selected == i)
			if isSel and colors and colors[i] then
				gfx.set(colors[i][1], colors[i][2], colors[i][3], 0.85)
				drawRect(sx, y, segW, h, true)
				gfx.set(1, 1, 1, 1)
			else
				gfx.set(0.18, 0.18, 0.18, 1)
				drawRect(sx, y, segW, h, true)
				gfx.set(1, 1, 1, 1)
			end
			drawRect(sx, y, segW, h, false)
			if hover then
				gfx.set(1,1,1,0.08)
				drawRect(sx, y, segW, h, true)
				gfx.set(1,1,1,1)
			end
			local tw = gfx.measurestr(labels[i] or '')
			drawText(sx + (segW - tw) / 2, y + h/2 - UI.font/2, labels[i] or '')
			if hover and UI.mouseDown and not UI.lastMouseDown then
				if selected ~= i then selected = i; changed = true end
			end
		end
		return selected, changed
	end

local function drawSlider(x, y, w, h, minV, maxV, value)
	-- Track bar
	gfx.set(0.15, 0.15, 0.15, 1)
	drawRect(x, y + h/2 - 2, w, 4, true)
	local t = (value - minV) / (maxV - minV)
	t = clamp(t, 0, 1)
	local knobX = x + t * w
	gfx.set(0.8, 0.8, 0.8, 1)
	drawRect(knobX - 5, y, 10, h, true)

	local changed = false
	if UI.mouseDown then
		if UI.dragging == nil and mouseInRect(x, y, w, h) and not UI.lastMouseDown and not UI.blockDragUntilMouseUp then
			UI.dragging = {
				type = 'slider', x = x, y = y, w = w, h = h,
				minV = minV, maxV = maxV,
				startMouseX = gfx.mouse_x, startVal = value,
				fineEngaged = false,
			}
		end
		if UI.dragging and UI.dragging.type == 'slider' and UI.dragging.x == x and UI.dragging.y == y then
			local shiftDown = (gfx.mouse_cap & 8) == 8
			-- Engage fine mode during this drag when Shift is pressed; stay until mouse released
			if shiftDown and not UI.dragging.fineEngaged then
				UI.dragging.fineEngaged = true
				UI.dragging.startMouseX = gfx.mouse_x
				UI.dragging.startVal = value
			end
			local newVal
			if UI.dragging.fineEngaged then
				local fineFactor = 0.1
				local rel = (gfx.mouse_x - UI.dragging.startMouseX) / w
				newVal = UI.dragging.startVal + rel * (maxV - minV) * fineFactor
				newVal = clamp(newVal, minV, maxV)
			else
				local rel = clamp((gfx.mouse_x - x) / w, 0, 1)
				newVal = minV + rel * (maxV - minV)
			end
			if newVal ~= value then
				value = newVal
				changed = true
			end
		end
	else
		if UI.dragging and UI.dragging.type == 'slider' and UI.dragging.x == x and UI.dragging.y == y then
			UI.dragging = nil
		end
	end
	return value, changed
end

local function drawLabeledSlider(label, x, y, w, h, minV, maxV, value, fmt)
	gfx.set(1, 1, 1, 1)
	drawText(x, y - UI.font - 2, string.format('%s: ' .. (fmt or '%.0f'), label, value))
	local v, ch = drawSlider(x, y, w, h, minV, maxV, value)
	return v, ch
end

local function applyFromUI()
	local p = {
		offsetMs = round(state.offsetMs),
		minVel = round(state.minVel),
		maxVel = round(state.maxVel),
		globalShift = round(state.globalShift),
		moveNoteOff = state.moveNoteOff and true or false,
	upStrum = state.upStrum and true or false,
		endShiftMs = round(state.endShiftMs),
		brushingMode = state.brushingMode and true or false,
	}
	commitApply(p, UI.previewApplyTiming ~= false)
	-- Flash green on Apply
	UI.flashColor = {0.10, 0.50, 0.10}
	UI.flashUntil = r.time_precise() + 0.12
end

local function resetDefaults()
	state.offsetMs = 20
	state.minVel = 80
	state.maxVel = 100
	state.globalShift = 0
	state.moveNoteOff = false
	state.upStrum = false
	state.endShiftMs = 0
end

local function loadSlot(slot)
	local preset = UI.presets[slot]
	if not preset then return end
	state.offsetMs = tonumber(preset.offsetMs) or state.offsetMs
	state.minVel = tonumber(preset.minVel) or state.minVel
	state.maxVel = tonumber(preset.maxVel) or state.maxVel
	state.globalShift = tonumber(preset.globalShift) or state.globalShift
	state.moveNoteOff = not not preset.moveNoteOff
	state.upStrum = not not preset.upStrum
	state.endShiftMs = tonumber(preset.endShiftMs) or state.endShiftMs
	state.brushingMode = not not preset.brushingMode
	-- Initialize length if brushing is on and value invalid
	if state.brushingMode and (not state.endShiftMs or state.endShiftMs <= 0) then
		state.endShiftMs = 20
	end
	-- Clear previous temporary saved shift (toggle history not applicable across preset loads)
	UI.prevEndShiftMs = nil
end

local function saveSlot(slot)
	UI.presets[slot] = {
		offsetMs = round(state.offsetMs),
		minVel = round(state.minVel),
		maxVel = round(state.maxVel),
		globalShift = round(state.globalShift),
		moveNoteOff = state.moveNoteOff and true or false,
		upStrum = state.upStrum and true or false,
	endShiftMs = round(state.endShiftMs),
	brushingMode = state.brushingMode and true or false,
	}
	savePresets(UI.presets)
end

local function initWindow()
	gfx.setfont(1, 'Arial', UI.font)
	gfx.init('Strum Editor (v2.0.0)', UI.w, UI.h)
	-- Always on top (topmost) using gfx.dock flag 0x100
	local dk = gfx.dock(-1)
	if (dk & 0x100) == 0x000 then gfx.dock(dk | 0x100) end
end

-- Optionally keep focus on our window using JS_ReaScriptAPI if available
local function ensureTopMostAndFocus()
	-- Re-assert topmost in case dock flags changed
	local dk = gfx.dock(-1)
	if (dk & 0x100) == 0 then gfx.dock(dk | 0x100) end

	-- Focus steal back to Strum Editor if JS API exists
	if reaper and reaper.JS_Window_Find and reaper.JS_Window_SetFocus and reaper.JS_Window_GetFocus then
		if not UI.hwnd then
			UI.hwnd = reaper.JS_Window_Find('Strum Editor (v2.0.0)', true)
		end
		if UI.hwnd then
			local cur = reaper.JS_Window_GetFocus()
			if cur ~= UI.hwnd then
				-- Suppress focus stealing while user is interacting outside
				local mouseDown = false
				if reaper.JS_Mouse_GetState then
					local m = reaper.JS_Mouse_GetState(0)
					mouseDown = (m ~= 0)
				end
				local keyDown = false
				if reaper.JS_VKeys_GetState then
					local vk = reaper.JS_VKeys_GetState(0)
					if type(vk) == 'string' and #vk >= 256 then
						for i = 1, 256 do
							if string.byte(vk, i) ~= 0 then keyDown = true; break end
						end
					end
				end
				if not mouseDown and not keyDown then
					reaper.JS_Window_SetFocus(UI.hwnd)
				end
			end
		end
	end
end

local function draw()
	local now = r.time_precise()
	if UI.flashUntil and now < UI.flashUntil then
		local c = UI.flashColor or {0.10, 0.50, 0.10}
		gfx.set(c[1], c[2], c[3], 1)
	elseif saveMode then
		gfx.set(0.8, 0.0, 0.0, 1)
	else
		gfx.set(0.08, 0.08, 0.08, 1)
	end
	drawRect(0, 0, gfx.w, gfx.h, true)
	gfx.set(1, 1, 1, 1)
	drawText(12, 8, 'Strum Editor (v2.0.0)')

	-- Top-right helper hints
	local hint1 = 'Reset Value (Ctrl+Click)'
	local hint2 = 'Fine Value (Shift+Click)'
	local h1w = gfx.measurestr(hint1)
	local h2w = gfx.measurestr(hint2)
	local pad = 12
	gfx.set(0.85, 0.85, 0.85, 0.9)
	drawText(gfx.w - h1w - pad, 8, hint1)
	drawText(gfx.w - h2w - pad, 8 + UI.font + 2, hint2)

	local x = 20
	local y = 40
	local sw = gfx.w - 40
	local sh = 20

		local changed = false
		local ch_offset = false
		local ch_min = false
		local ch_max = false
		local ch_global = false
		local ch_endShift = false
		local ch_dir = false
		local ch_moveOff = false
		do
			-- Ctrl+click resets Offset to 0
			local ctrlDown = (gfx.mouse_cap & 4) == 4
			if ctrlDown and UI.mouseDown and not UI.lastMouseDown and mouseInRect(x, y, sw, sh) then
				state.offsetMs = 0; changed = true; ch_offset = true; UI.blockDragUntilMouseUp = true
			end
			local v, ch = drawLabeledSlider('Offset/ms (Z/X)', x, y, sw, sh, 0, 400, state.offsetMs)
			state.offsetMs = clamp(v, 0, 1000); changed = changed or ch; ch_offset = ch or ch_offset; y = y + 50
		end
		do
			-- Ctrl+click resets ONLY the clicked velocity slider to 100
			local ctrlDown = (gfx.mouse_cap & 4) == 4
			local minAreaW = sw*0.5 - 10
			local maxAreaX = x + sw*0.5 + 10
			local maxAreaW = sw*0.5 - 10
			if ctrlDown and UI.mouseDown and not UI.lastMouseDown then
				if mouseInRect(x, y, minAreaW, sh) then
					state.minVel = math.min(100, state.maxVel)
					changed = true; ch_min = true; UI.blockDragUntilMouseUp = true
				elseif mouseInRect(maxAreaX, y, maxAreaW, sh) then
					state.maxVel = 100
					if state.minVel > state.maxVel then state.minVel = state.maxVel end
					changed = true; ch_max = true; UI.blockDragUntilMouseUp = true
				end
			end
			local v, ch = drawLabeledSlider('Min Velocity (D/F)', x, y, minAreaW, sh, 1, 127, state.minVel)
			state.minVel = clamp(v, 1, state.maxVel); changed = changed or ch; ch_min = ch or ch_min
			local v2, ch2 = drawLabeledSlider('Max Velocity (C/V)', maxAreaX, y, maxAreaW, sh, 1, 127, state.maxVel)
			state.maxVel = clamp(v2, 1, 127); changed = changed or ch2; ch_max = ch2 or ch_max
			if state.minVel > state.maxVel then state.minVel = state.maxVel; changed = true end
			y = y + 50
		end
		do
			-- Ctrl+click resets to 0
			local ctrlDown = (gfx.mouse_cap & 4) == 4
			if ctrlDown and UI.mouseDown and not UI.lastMouseDown and mouseInRect(x, y, sw, sh) then
				state.globalShift = 0; changed = true; ch_global = true; UI.blockDragUntilMouseUp = true
			end
			local v, ch = drawLabeledSlider('Global Vel Shift (B/N)', x, y, sw, sh, -127, 127, state.globalShift)
			state.globalShift = clamp(v, -127, 127); changed = changed or ch; ch_global = ch or ch_global; y = y + 40
		end
			do
				-- Ctrl+click resets to 0
				local ctrlDown = (gfx.mouse_cap & 4) == 4
				if ctrlDown and UI.mouseDown and not UI.lastMouseDown and mouseInRect(x, y, sw, sh) then
					state.endShiftMs = 0; changed = true; ch_endShift = true; UI.blockDragUntilMouseUp = true
				end
				-- In brushing mode, treat as absolute note length (ms) from note-on (0ms)
				local label = state.brushingMode and 'Note Off Length/ms (G/H)' or 'Note Off Shift/ms (G/H)'
				local minV, maxV = state.brushingMode and 1 or -400, 400
				local v, ch = drawLabeledSlider(label, x, y, sw, sh, minV, maxV, state.endShiftMs)
				if state.brushingMode then
					state.endShiftMs = clamp(v, 1, 400)
				else
					state.endShiftMs = clamp(v, -400, 400)
				end
				changed = changed or ch; ch_endShift = ch or ch_endShift; y = y + 40
			end

			-- Direction segmented toggle (Up: red, Down: green)
			gfx.set(1, 1, 1, 1)
			drawText(x, y - 4, 'Direction (E)')
		local dirSel = state.upStrum and 1 or 2 -- 1=Up, 2=Down
		dirSel = select(1, drawSegmentToggle2(x + 90, y - 6, 180, 24, {'Up', 'Down'}, dirSel, { {0.85,0.25,0.25}, {0.2,0.75,0.2} }))
	local newUp = (dirSel == 1)
	if newUp ~= state.upStrum then state.upStrum = newUp; changed = true; ch_dir = true end
		y = y + 30

			-- Move Note Off segmented toggle
			gfx.set(1, 1, 1, 1)
			drawText(x, y - 4, 'Move Note Off (Q)')
		local offSel = state.moveNoteOff and 2 or 1 -- 1=Off, 2=On
			local newSel = select(1, drawSegmentToggle2(x + 120, y - 6, 150, 24, {'Off', 'On'}, offSel, { {0.85,0.25,0.25}, {0.2,0.75,0.2} }))
	local newMove = (newSel == 2)
		if newMove ~= state.moveNoteOff then
			state.moveNoteOff = newMove; changed = true; ch_moveOff = true
			if state.moveNoteOff then state.endShiftMs = 0 end
		end
		y = y + 30

		-- Brushing mode toggle
		gfx.set(1, 1, 1, 1)
		drawText(x, y - 4, 'Brushing Mode (W)')
		local bSel = state.brushingMode and 2 or 1 -- 1=Off, 2=On
		local bNew = select(1, drawSegmentToggle2(x + 160, y - 6, 110, 24, {'Off', 'On'}, bSel, { {0.85,0.25,0.25}, {0.2,0.75,0.2} }))
		local brushingNew = (bNew == 2)
		if brushingNew ~= state.brushingMode then
			-- Save/restore Note Off Shift when toggling brushing mode without apply
			if brushingNew then
				UI.prevEndShiftMs = state.endShiftMs
				if not state.endShiftMs or state.endShiftMs <= 0 then state.endShiftMs = 20 end
			else
				if UI.prevEndShiftMs ~= nil then
					state.endShiftMs = UI.prevEndShiftMs
					UI.prevEndShiftMs = nil
				end
			end
			state.brushingMode = brushingNew; changed = true
			-- Do not force preview timing here; Offset slider will set it when needed
		end
		y = y + 30

		-- Ruler-overlap auto select toggle
		gfx.set(1, 1, 1, 1)
		drawText(x, y - 4, 'Ruler Select (T)')
		local rsSel = state.rulerSelectMode and 2 or 1 -- 1=Off, 2=On
		local rsNew = select(1, drawSegmentToggle2(x + 150, y - 6, 110, 24, {'Off', 'On'}, rsSel, { {0.85,0.25,0.25}, {0.2,0.75,0.2} }))
		local rulerNew = (rsNew == 2)
		if rulerNew ~= state.rulerSelectMode then
			state.rulerSelectMode = rulerNew; changed = true
			-- Persist setting immediately
			saveSettings({ rulerSelectMode = state.rulerSelectMode })
		end
		y = y + 30

		-- Buttons (centered)
	local by = gfx.h - 40
	local btnW = {90, 100, 100, 120}
	local gap = 10
	local totalW = btnW[1] + btnW[2] + btnW[3] + btnW[4] + gap * 3
	local startX = math.floor((gfx.w - totalW) / 2)
	local bx = startX
			if drawButton(bx, by, btnW[1], 24, 'Apply (A)') then applyFromUI() end
			bx = bx + btnW[1] + gap
			if drawButton(bx, by, btnW[2], 24, 'Undo (R)') then
				-- Cancel any transient preview/hover first
				if UI._hoverPrev and UI._hoverPrev.active then
					restoreHoverSnapshot()
					UI._hoverPrev.active = false
					UI._hoverPrev.index = nil
				end
				if previewActive then
					restoreSnapshot(true)
					previewActive = false
				end
				-- Perform REAPER Undo
				if r.Undo_DoUndo2 then r.Undo_DoUndo2(0) else r.Main_OnCommand(40029, 0) end
				-- Refresh snapshot and UI state
				captureSnapshot(); setBaseFromCurrentSnapshot(); inferDefaultsFromSnapshot()
				UI.flashColor = {0.60, 0.10, 0.10}
				UI.flashUntil = r.time_precise() + 0.12
			end
			bx = bx + btnW[2] + gap
			if drawButton(bx, by, btnW[3], 24, 'Close (Esc)') then return 'close' end
			bx = bx + btnW[3] + gap
			if drawButton(bx, by, btnW[4], 24, 'Enter (Enter)') then return 'apply_close' end

		-- Preset save/load row: buttons 1..0 (0=slot10)
			local rowY = gfx.h - 75
			local btnW, btnH = 28, 24
			local gap = 6
			local startX = gfx.w - (10 * btnW + 9 * gap) - 20
		-- Left labels: Save and Load
		local rowLabel = 'Save : (S)     Load : (0～9)'
		local tw = gfx.measurestr(rowLabel)
		drawText(startX - tw - 10, rowY + btnH/2 - UI.font/2, rowLabel)

		local anyHover = false
		for i = 1, 10 do
			local sLabel = (i < 10) and tostring(i) or '0'
			local preset = UI.presets[i]
			local color = nil
			if type(preset) == 'table' then
				if preset.upStrum == true then
					color = {0.85, 0.25, 0.25} -- Up = red
				elseif preset.upStrum == false then
					color = {0.2, 0.75, 0.2} -- Down = green
				end
			end
			local bx = startX + (i - 1) * (btnW + gap)
			local by2 = rowY
			local isHovering = mouseInRect(bx, by2, btnW, btnH)
			-- Hover preview: when entering hover, capture snapshot and preview preset (if exists).
			-- When leaving hover, restore.
			UI._hoverPrev = UI._hoverPrev or { index = nil, active = false }
			if isHovering then
				anyHover = true
				if UI._hoverPrev.index ~= i then
					-- Hover moved to a new preset button
					if not UI._hoverPrev.active then
						captureHoverSnapshotFromCurrent()
						UI._hoverPrev.active = true
					end
					UI._hoverPrev.index = i
					if UI.presets[i] then
						-- Ensure main snapshot is fresh before previewing
						if UI._hoverPrev.lastPreviewed ~= i then
							captureSnapshot(); setBaseFromCurrentSnapshot()
						end
						-- Load into state and preview (timing included)
						loadSlot(i)
						local p = {
							offsetMs = round(state.offsetMs),
							minVel = round(state.minVel),
							maxVel = round(state.maxVel),
							globalShift = round(state.globalShift),
							moveNoteOff = state.moveNoteOff,
							upStrum = state.upStrum,
							endShiftMs = round(state.endShiftMs),
							brushingMode = state.brushingMode,
						}
						previewUpdate(p, true)
						UI._hoverPrev.lastPreviewed = i
					end
				end
			end

			if drawButtonColored(bx, rowY, btnW, btnH, sLabel, color) then
				local shiftDown = (gfx.mouse_cap & 8) == 8
				if shiftDown then
					-- Shift+Click: delete with confirmation
					if UI.presets[i] then
						local res = r.MB('Do you want to delete the preset? (Slot: '..sLabel..')', 'Strum Editor (v2.0.0)', 4)
						if res == 6 then -- Yes
							UI.presets[i] = nil
							savePresets(UI.presets)
						end
					end
				else
					-- Click: save if empty, otherwise load (preview only)
					if UI.presets[i] then
						loadSlot(i)
						local p = {
							offsetMs = round(state.offsetMs),
							minVel = round(state.minVel),
							maxVel = round(state.maxVel),
							globalShift = round(state.globalShift),
							moveNoteOff = state.moveNoteOff,
							upStrum = state.upStrum,
							endShiftMs = round(state.endShiftMs),
							brushingMode = state.brushingMode,
						}
						previewUpdate(p, true)
						-- feedback (light flash)
						UI.flashColor = {0.10, 0.50, 0.10}
						UI.flashUntil = r.time_precise() + 0.08
					else
						saveSlot(i)
						UI.flashColor = {0.10, 0.50, 0.10}
						UI.flashUntil = r.time_precise() + 0.08
					end
				end
			end
		end
		-- If not hovering any preset button, restore from hover snapshot (if active)
		if not anyHover and UI._hoverPrev and UI._hoverPrev.active then
			restoreHoverSnapshot()
			UI._hoverPrev.active = false
			UI._hoverPrev.index = nil
			-- Re-capture main snapshot/state to keep preview system coherent
			captureSnapshot()
			setBaseFromCurrentSnapshot()
			inferDefaultsFromSnapshot()
		end

		-- Footer removed per request

	-- Decide whether preview should move note-on timing this frame
	UI.previewApplyTiming = (ch_offset or ch_dir) and true or false
	return changed and 'changed' or 'ok'
end

-- "ルーラー（編集カーソル）に重なるノートのみ選択"を実現するユーティリティ
local RULER_SEL_CACHE = { take = nil, ppq = nil }
local RULER_SEL_DEBOUNCE = { active = false, startedAt = 0, ppq = nil, selectionCleared = false }
local function updateSelectionByCursor()
	if not state.rulerSelectMode then return end
	local take = getActiveMIDITake()
	if not take then return end

	local curTime = r.GetCursorPosition() -- project seconds
	local curPPQ = r.MIDI_GetPPQPosFromProjTime(take, curTime)
	local now = r.time_precise()

	local cursorChanged = not (RULER_SEL_CACHE.take == take and RULER_SEL_CACHE.ppq and curPPQ == RULER_SEL_CACHE.ppq)

	-- カーソルが動いたら即座に全選択を解除し、20ms後に確定選択を行う準備
	if cursorChanged then
		local _, noteCount = r.MIDI_CountEvts(take)
		if noteCount > 0 then
			if not RULER_SEL_DEBOUNCE.selectionCleared then
				r.PreventUIRefresh(1)
				for i = 0, noteCount - 1 do
					local ok, sel, muted, sppq, eppq, chan, pitch, vel = r.MIDI_GetNote(take, i)
					if ok and sel then
						r.MIDI_SetNote(take, i, false, muted, sppq, eppq, chan, pitch, vel, true)
					end
				end
				r.PreventUIRefresh(-1)
				RULER_SEL_DEBOUNCE.selectionCleared = true
			end
		end
		RULER_SEL_DEBOUNCE.active = true
		RULER_SEL_DEBOUNCE.startedAt = now
		RULER_SEL_DEBOUNCE.ppq = curPPQ
		RULER_SEL_CACHE.take = take
		RULER_SEL_CACHE.ppq = curPPQ
		return
	end

	-- 20ms 経過で確定選択を実行
	if RULER_SEL_DEBOUNCE.active and (now - RULER_SEL_DEBOUNCE.startedAt) >= 0.020 then
		local _, noteCount = r.MIDI_CountEvts(take)
		if noteCount > 0 then
			r.PreventUIRefresh(1)
			for i = 0, noteCount - 1 do
				local ok, sel, muted, sppq, eppq, chan, pitch, vel = r.MIDI_GetNote(take, i)
				if ok then
					local overlap = (RULER_SEL_DEBOUNCE.ppq >= sppq) and (RULER_SEL_DEBOUNCE.ppq < eppq)
					if sel ~= overlap then
						r.MIDI_SetNote(take, i, overlap, muted, sppq, eppq, chan, pitch, vel, true)
					end
				end
			end
			r.PreventUIRefresh(-1)
		end
		RULER_SEL_DEBOUNCE.active = false
		RULER_SEL_DEBOUNCE.selectionCleared = false
	end
end

local function mainloop()
	UI.lastMouseDown = UI.mouseDown
	UI.mouseDown = (gfx.mouse_cap & 1) == 1
	if UI.lastMouseDown and not UI.mouseDown then UI.blockDragUntilMouseUp = false end

	-- Keep window always on top and focused (if JS API is available)
	ensureTopMostAndFocus()

			local result = draw()
			if result == 'close' then
				-- Ensure hover preview is undone
				if UI._hoverPrev and UI._hoverPrev.active then
					restoreHoverSnapshot()
					UI._hoverPrev.active = false
					UI._hoverPrev.index = nil
				end
				if previewActive then restoreSnapshot() end
				if SNAPSHOT.take then r.MIDI_Sort(SNAPSHOT.take) end
				return
			end
			if result == 'apply_close' then
				local p = {
					offsetMs = round(state.offsetMs),
					minVel = round(state.minVel),
					maxVel = round(state.maxVel),
					globalShift = round(state.globalShift),
					moveNoteOff = state.moveNoteOff,
					upStrum = state.upStrum,
					endShiftMs = round(state.endShiftMs),
					brushingMode = state.brushingMode,
				}
				commitApply(p, UI.previewApplyTiming ~= false)
				UI.flashColor = {0.10, 0.50, 0.10}
				UI.flashUntil = r.time_precise() + 0.12
				return
			end

		if result == 'changed' then
			local p = {
				offsetMs = round(state.offsetMs),
				minVel = round(state.minVel),
				maxVel = round(state.maxVel),
				globalShift = round(state.globalShift),
				moveNoteOff = state.moveNoteOff,
				upStrum = state.upStrum,
			endShiftMs = round(state.endShiftMs),
			brushingMode = state.brushingMode,
			}
			previewUpdate(p, UI.previewApplyTiming)
		end

	-- Key handling
		local c = gfx.getchar()
		if c == -1 then -- Window X (close)
			if UI._hoverPrev and UI._hoverPrev.active then
				restoreHoverSnapshot()
				UI._hoverPrev.active = false
				UI._hoverPrev.index = nil
			end
			if previewActive then restoreSnapshot() end
			if SNAPSHOT.take then r.MIDI_Sort(SNAPSHOT.take) end
			return
		end
		if c == 27 then -- ESC
			if UI._hoverPrev and UI._hoverPrev.active then
				restoreHoverSnapshot()
				UI._hoverPrev.active = false
				UI._hoverPrev.index = nil
			end
			if previewActive then restoreSnapshot() end
			if SNAPSHOT.take then r.MIDI_Sort(SNAPSHOT.take) end
			return
		end
		if c == 13 then -- Enter key
			local p = {
				offsetMs = round(state.offsetMs),
				minVel = round(state.minVel),
				maxVel = round(state.maxVel),
				globalShift = round(state.globalShift),
				moveNoteOff = state.moveNoteOff,
				upStrum = state.upStrum,
				endShiftMs = round(state.endShiftMs),
				brushingMode = state.brushingMode,
			}
			commitApply(p, UI.previewApplyTiming ~= false)
			-- Flash green on Apply+Close (Enter)
			UI.flashColor = {0.10, 0.50, 0.10}
			UI.flashUntil = r.time_precise() + 0.12
			return
		end
		if c == string.byte('a') or c == string.byte('A') then
			applyFromUI()
			-- applyFromUI triggers green flash already
		end
			if c == string.byte('s') or c == string.byte('S') then
				-- Toggle save mode (red background)
				saveMode = not saveMode
			end
			if c == string.byte('r') or c == string.byte('R') then
				-- Undo: cancel transient preview, then trigger REAPER Undo
				if UI._hoverPrev and UI._hoverPrev.active then
					restoreHoverSnapshot()
					UI._hoverPrev.active = false
					UI._hoverPrev.index = nil
				end
				if previewActive then
					restoreSnapshot(true)
					previewActive = false
				end
				if r.Undo_DoUndo2 then r.Undo_DoUndo2(0) else r.Main_OnCommand(40029, 0) end
				captureSnapshot(); setBaseFromCurrentSnapshot(); inferDefaultsFromSnapshot()
				UI.flashColor = {0.60, 0.10, 0.10}
				UI.flashUntil = r.time_precise() + 0.12
			end
			if c == string.byte('e') or c == string.byte('E') then
				state.upStrum = not state.upStrum
				local p = {
					offsetMs = round(state.offsetMs),
					minVel = round(state.minVel),
					maxVel = round(state.maxVel),
					globalShift = round(state.globalShift),
					moveNoteOff = state.moveNoteOff,
						upStrum = state.upStrum,
						endShiftMs = round(state.endShiftMs),
						brushingMode = state.brushingMode,
				}
				previewUpdate(p, true)
			end
			if c == string.byte('q') or c == string.byte('Q') then
				state.moveNoteOff = not state.moveNoteOff
				if state.moveNoteOff then state.endShiftMs = 0 end
				local p = {
					offsetMs = round(state.offsetMs),
					minVel = round(state.minVel),
					maxVel = round(state.maxVel),
					globalShift = round(state.globalShift),
					moveNoteOff = state.moveNoteOff,
						upStrum = state.upStrum,
						endShiftMs = round(state.endShiftMs),
						brushingMode = state.brushingMode,
				}
				previewUpdate(p, false)
			end

			-- Ruler Select toggle (T)
			if c == string.byte('t') or c == string.byte('T') then
				state.rulerSelectMode = not state.rulerSelectMode
				if state.rulerSelectMode then
					-- 強制的に選択を現在カーソル位置で再構成
					RULER_SEL_CACHE.take, RULER_SEL_CACHE.ppq = nil, nil
					RULER_SEL_DEBOUNCE.active = false
					RULER_SEL_DEBOUNCE.selectionCleared = false
					updateSelectionByCursor()
				end
				-- Persist setting on toggle
				saveSettings({ rulerSelectMode = state.rulerSelectMode })
			end

			-- Slider hotkeys: left key decreases, right key increases
			local function nudge(val, step, lo, hi)
				local v = clamp(val + step, lo, hi)
				return v, v ~= val
			end
			local nudged = false
			-- Offset (Z/X)
			if c == string.byte('z') or c == string.byte('Z') then
				state.offsetMs, nudged = nudge(state.offsetMs, -1, 0, 400)
			elseif c == string.byte('x') or c == string.byte('X') then
				state.offsetMs, nudged = nudge(state.offsetMs, 1, 0, 400)
			end
			if nudged then
				previewUpdate({
					offsetMs = round(state.offsetMs),
					minVel = round(state.minVel),
					maxVel = round(state.maxVel),
					globalShift = round(state.globalShift),
					moveNoteOff = state.moveNoteOff,
					upStrum = state.upStrum,
					endShiftMs = round(state.endShiftMs),
					brushingMode = state.brushingMode,
				}, true)
			end

			-- Min (D/F)
			nudged = false
			if c == string.byte('d') or c == string.byte('D') then
				state.minVel, nudged = nudge(state.minVel, -1, 1, state.maxVel)
			elseif c == string.byte('f') or c == string.byte('F') then
				state.minVel, nudged = nudge(state.minVel, 1, 1, state.maxVel)
			end
			if nudged then
				previewUpdate({
					offsetMs = round(state.offsetMs),
					minVel = round(state.minVel),
					maxVel = round(state.maxVel),
					globalShift = round(state.globalShift),
					moveNoteOff = state.moveNoteOff,
					upStrum = state.upStrum,
					endShiftMs = round(state.endShiftMs),
					brushingMode = state.brushingMode,
				}, false)
			end

			-- Max (C/V)
			nudged = false
			if c == string.byte('c') or c == string.byte('C') then
				state.maxVel, nudged = nudge(state.maxVel, -1, 1, 127)
			elseif c == string.byte('v') or c == string.byte('V') then
				state.maxVel, nudged = nudge(state.maxVel, 1, 1, 127)
			end
			if nudged then
				if state.minVel > state.maxVel then state.minVel = state.maxVel end
				previewUpdate({
					offsetMs = round(state.offsetMs),
					minVel = round(state.minVel),
					maxVel = round(state.maxVel),
					globalShift = round(state.globalShift),
					moveNoteOff = state.moveNoteOff,
					upStrum = state.upStrum,
					endShiftMs = round(state.endShiftMs),
					brushingMode = state.brushingMode,
				}, false)
			end

			-- Global (B/N)
			nudged = false
			if c == string.byte('b') or c == string.byte('B') then
				state.globalShift, nudged = nudge(state.globalShift, -1, -127, 127)
			elseif c == string.byte('n') or c == string.byte('N') then
				state.globalShift, nudged = nudge(state.globalShift, 1, -127, 127)
			end
			if nudged then
				previewUpdate({
					offsetMs = round(state.offsetMs),
					minVel = round(state.minVel),
					maxVel = round(state.maxVel),
					globalShift = round(state.globalShift),
					moveNoteOff = state.moveNoteOff,
					upStrum = state.upStrum,
					endShiftMs = round(state.endShiftMs),
					brushingMode = state.brushingMode,
				}, false)
			end

			-- Note Off Shift (G/H)
			nudged = false
			if c == string.byte('g') or c == string.byte('G') then
				state.endShiftMs, nudged = nudge(state.endShiftMs, -1, -400, 400)
			elseif c == string.byte('h') or c == string.byte('H') then
				state.endShiftMs, nudged = nudge(state.endShiftMs, 1, -400, 400)
			end
			if nudged then
				previewUpdate({
					offsetMs = round(state.offsetMs),
					minVel = round(state.minVel),
					maxVel = round(state.maxVel),
					globalShift = round(state.globalShift),
					moveNoteOff = state.moveNoteOff,
					upStrum = state.upStrum,
					endShiftMs = round(state.endShiftMs),
					brushingMode = state.brushingMode,
				}, false)
			end

			-- Brushing Mode toggle (W)
			if c == string.byte('w') or c == string.byte('W') then
				local newMode = not state.brushingMode
				-- Save/restore endShift when toggling via key without applying
				if newMode then
					UI.prevEndShiftMs = state.endShiftMs
					if not state.endShiftMs or state.endShiftMs <= 0 then state.endShiftMs = 20 end
				else
					if UI.prevEndShiftMs ~= nil then
						state.endShiftMs = UI.prevEndShiftMs
						UI.prevEndShiftMs = nil
					end
				end
				state.brushingMode = newMode
				-- Preview with non-timing changes only (do not move note-on)
				UI.previewApplyTiming = false
				previewUpdate({
					offsetMs = round(state.offsetMs),
					minVel = round(state.minVel),
					maxVel = round(state.maxVel),
					globalShift = round(state.globalShift),
					moveNoteOff = state.moveNoteOff,
					upStrum = state.upStrum,
					endShiftMs = round(state.endShiftMs),
					brushingMode = state.brushingMode,
				}, false)
			end
				-- Numeric presets: 1..9,0 (0 => slot 10)
				if c and c >= 48 and c <= 57 then
					local slot = (c == 48) and 10 or (c - 48)
					if saveMode then
						-- Save to slot and exit save mode
						saveSlot(slot)
						saveMode = false
					else
						-- Load -> apply (keep window open)
						if UI.presets[slot] then
							loadSlot(slot)
										local p = {
								offsetMs = round(state.offsetMs),
								minVel = round(state.minVel),
								maxVel = round(state.maxVel),
								globalShift = round(state.globalShift),
								moveNoteOff = state.moveNoteOff,
										upStrum = state.upStrum,
													endShiftMs = round(state.endShiftMs),
													brushingMode = state.brushingMode,
							}
								-- Use applyTiming according to whether timing controls are in preset; conservatively true
								commitApply(p, true)
								UI.flashColor = {0.10, 0.50, 0.10}
								UI.flashUntil = r.time_precise() + 0.12
						end
					end
				end

    gfx.update()
		-- オート選択モード: まずカーソル位置で選択を更新
		if state.rulerSelectMode then
			updateSelectionByCursor()
		end
		-- Monitor selection changes and update preview accordingly
		monitorSelectionAndPreview()
	r.defer(mainloop)
end

-- Entry
initWindow()
	-- Capture target and show initial preview
		if captureSnapshot() then
			-- Initialize state from current selection; do NOT preview automatically
			inferDefaultsFromSnapshot()
			setBaseFromCurrentSnapshot()
		end
mainloop()


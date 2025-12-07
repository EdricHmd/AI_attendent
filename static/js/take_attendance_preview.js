(function () {
    // take_attendance_preview.js - logic for teacher/take_attendance.html
    // Performance knobs (tune as needed)
    const CAPTURE = {
        width: 400,         // further downscale to reduce bandwidth/CPU
        height: 300,
        jpegQuality: 0.55,  // slight reduction in quality
        intervalMs: 1600    // send every 1.6s; skip if previous is in-flight
    };
    const cfg = window.TAKE_ATTENDANCE_CONFIG || {};
    const video = document.getElementById('preview');
    const btnStart = document.getElementById('btnStart');
    const btnStop = document.getElementById('btnStop');
    const btnStartSrv = document.getElementById('btnStartSrv');
    const btnStopSrv = document.getElementById('btnStopSrv');
    const btnStartBrowser = document.getElementById('btnStartBrowser');
    const btnStopBrowser = document.getElementById('btnStopBrowser');
    // show model readiness in the preview card
    const previewStatus = document.getElementById('previewStatus');
    let stream = null;
    let browserInterval = null;
    let countdownInterval = null;
    const countdownEl = document.getElementById('sessionCountdown');
    // Persist seen students per session so navigation doesn't cause duplicate UI
    // entries or resends. Stored in sessionStorage under key `seen:{sessionId}`.
    function seenKey(sessionId) {
        return `seen:${sessionId || 'null'}`;
    }
    function loadSeen(sessionId) {
        try {
            const raw = sessionStorage.getItem(seenKey(sessionId));
            if (!raw) return new Set();
            const arr = JSON.parse(raw);
            return new Set(arr || []);
        } catch (e) { console.debug('loadSeen failed', e); return new Set(); }
    }
    function saveSeen(set, sessionId) {
        try {
            const arr = Array.from(set);
            sessionStorage.setItem(seenKey(sessionId), JSON.stringify(arr));
        } catch (e) { console.debug('saveSeen failed', e); }
    }
    let seenThisSession = loadSeen(window.currentSessionId);
    // map track_id -> last known match { student_id, name, mssv, ts }
    const lastMatchByTrack = {};
    const MATCH_HOLD_MS = 2000; // ms to keep showing last-known label when current frame has no match
    // internal capture canvas (off-screen) and overlay canvas (on top of video)
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    const overlay = document.getElementById('previewOverlay');
    const octx = overlay ? overlay.getContext('2d') : null;

    async function call(url) {
        const r = await fetch(url, { method: 'POST' });
        return await r.json();
    }

    async function startLocalPreview() {
        if (stream) return;
        try {
            stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
            video.srcObject = stream;
            try { await video.play(); } catch (e) { console.debug('video.play failed', e); }
            // size overlay to video
            if (overlay) {
                overlay.width = video.clientWidth || video.videoWidth || overlay.clientWidth || CAPTURE.width;
                overlay.height = video.clientHeight || video.videoHeight || overlay.clientHeight || CAPTURE.height;
            }
            if (btnStart) btnStart.disabled = true;
            if (btnStop) btnStop.disabled = false;
        } catch (e) {
            console.error('Cannot open webcam', e);
            alert('Không thể mở webcam: ' + (e && e.message ? e.message : e));
            if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; video.srcObject = null; }
        }
    }

    // helper to apply session flags to the UI (no start/end split)
    function applySessionFlagsToUI(session) {
        try {
            if (!session) {
                // clear countdown when no session
                if (countdownEl) countdownEl.textContent = '';
                stopCountdown();
                return;
            }
        } catch (e) { console.debug('applySessionFlagsToUI failed', e); }
    }

    // initialize session from server-provided config or hidden input so window.currentSessionId is set
    try {
        let initSessionId = null;
        if (cfg && cfg.currentSession && cfg.currentSession.id) {
            initSessionId = cfg.currentSession.id;
        } else {
            const hiddenSid = document.getElementById('current_session_id');
            if (hiddenSid && hiddenSid.value) initSessionId = hiddenSid.value;
        }
        if (initSessionId) {
            window.currentSessionId = initSessionId;
            const list = document.getElementById('attendanceList');
            let el = document.getElementById('sessionInfo');
            if (!el && list && list.parentNode) {
                el = document.createElement('div'); el.id = 'sessionInfo'; el.className = 'mb-2 small text-muted';
                list.parentNode.insertBefore(el, list);
            }
            if (el) {
                let sn = (cfg && cfg.currentSession) ? cfg.currentSession.session_number : '';
                let tb = (cfg && cfg.currentSession) ? cfg.currentSession.time_bucket : '';
                if (!sn) {
                    const hidSn = document.getElementById('current_session_number');
                    if (hidSn && hidSn.value) sn = hidSn.value;
                }
                if (!tb) {
                    const hidTb = document.getElementById('current_session_bucket');
                    if (hidTb && hidTb.value) tb = hidTb.value;
                }
                el.textContent = `Buổi: ${sn || '?'} · Khung: ${tb || '?'}`;
                // countdown element is already in the HTML template, just update it
                try {
                    const existingCountdown = document.getElementById('sessionCountdown');
                    if (existingCountdown) {
                        existingCountdown.textContent = '';
                    }
                } catch (e) { console.debug('init countdown element failed', e); }
            }
            try { if (cfg && cfg.currentSession) applySessionFlagsToUI(cfg.currentSession); } catch (e) { console.debug('applySessionFlagsToUI failed during init', e); }
            // if a session was preloaded, start countdown based on cfg.currentSession
            try {
                if (cfg && cfg.currentSession && cfg.currentSession.time_bucket && cfg.currentSession.session_open) {
                    const sessDate = cfg.currentSession.session_date || null;
                    const end = getBucketEnd(cfg.currentSession.time_bucket, sessDate);
                    startCountdown(end);
                }
            } catch (e) { console.debug('init countdown failed', e); }

            // Disable start button if session is already ended
            try {
                if (cfg && cfg.currentSession && !cfg.currentSession.session_open) {
                    if (btnStartBrowser) btnStartBrowser.disabled = true;
                    if (btnStart) btnStart.disabled = true;
                }
            } catch (e) { console.debug('disable buttons for ended session failed', e); }
        }
    } catch (e) { console.debug('init session failed', e); }

    function stopLocalPreview() {
        if (browserInterval) { clearInterval(browserInterval); browserInterval = null; }
        if (stream) {
            try { stream.getTracks().forEach(t => t.stop()); } catch (e) { console.debug('stop tracks failed', e); }
            stream = null;
        }
        if (video) video.srcObject = null;
        if (octx) { octx.clearRect(0, 0, overlay.width || 0, overlay.height || 0); }
        if (btnStart) btnStart.disabled = false;
        if (btnStop) btnStop.disabled = true;
        if (btnStartBrowser) btnStartBrowser.disabled = false;
        if (btnStopBrowser) btnStopBrowser.disabled = true;
        try { saveSeen(seenThisSession, window.currentSessionId); } catch (e) { }
        seenThisSession = new Set();
    }

    function getBucketEnd(bucket, sessionDate) {
        const d = sessionDate ? new Date(sessionDate + 'T00:00:00') : new Date();
        const year = d.getFullYear(), month = d.getMonth(), day = d.getDate();
        if (bucket === 'morning') {
            return new Date(year, month, day, 12, 0, 0);
        } else if (bucket === 'afternoon') {
            return new Date(year, month, day, 17, 0, 0);
        } else {
            return new Date(year, month, day, 23, 59, 59);
        }
    }

    function startCountdown(endDate) {
        stopCountdown();
        if (!endDate || !countdownEl) return;
        function tick() {
            const now = new Date();
            const diff = endDate - now;
            if (diff <= 0) {
                stopCountdown();
                
                // Check if attendance session was started (has session_id)
                if (!window.currentSessionId) {
                    // No session started - delete the unused session and redirect
                    if (countdownEl) countdownEl.textContent = 'Đang xóa buổi chưa sử dụng...';
                    console.log('[AUTO-DELETE] Countdown ended but no session was started - deleting unused session');
                    
                    // Disable start button
                    if (btnStartBrowser) btnStartBrowser.disabled = true;
                    if (btnStart) btnStart.disabled = true;
                    
                    // Call API to delete unused session
                    (async () => {
                        try {
                            const classId = (cfg && cfg.class_section_id) ? cfg.class_section_id : 
                                           (document.getElementById('class_section_id')?.value || '');
                            if (!classId) return;
                            
                            const deleteUrl = `/teacher/delete_unused_session/${classId}`;
                            const res = await fetch(deleteUrl, { method: 'POST' });
                            const data = await res.json();
                            
                            console.log('[AUTO-DELETE] Delete response:', data);
                            
                            if (data && data.ok) {
                                if (countdownEl) countdownEl.textContent = 'Đã xóa buổi chưa sử dụng';
                                // Redirect to sessions list after short delay
                                setTimeout(() => {
                                    if (cfg.urls && cfg.urls.sessionsList) {
                                        window.location.href = cfg.urls.sessionsList;
                                    }
                                }, 1500);
                            } else {
                                if (countdownEl) countdownEl.textContent = 'Thời gian đã hết';
                            }
                        } catch (e) {
                            console.warn('[AUTO-DELETE] Failed:', e);
                            if (countdownEl) countdownEl.textContent = 'Thời gian điểm danh đã hết';
                        }
                    })();
                    
                    return; // Exit without calling attendance APIs
                }
                
                // Session exists - proceed with auto-end
                if (countdownEl) countdownEl.textContent = 'Đang tự động kết thúc...';
                
                // auto-end session on timeout - mimics manual "Kết thúc điểm danh" button
                (async () => {
                    if (!cfg.urls || !cfg.urls.stopAttendance) return;
                    try {
                        console.log('[AUTO-END] Countdown reached 0, stopping attendance...');
                        
                        // Stop camera and browser interval first
                        if (browserInterval) { clearInterval(browserInterval); browserInterval = null; }
                        if (stream) { 
                            stream.getTracks().forEach(t => t.stop()); 
                            stream = null; 
                        }
                        if (video) video.srcObject = null;
                        if (octx) octx.clearRect(0, 0, overlay.width || 0, overlay.height || 0);
                        
                        // Disable buttons
                        if (btnStartBrowser) btnStartBrowser.disabled = true;
                        if (btnStopBrowser) btnStopBrowser.disabled = true;
                        if (btnStart) btnStart.disabled = true;
                        
                        // Call stop attendance API
                        const form = new FormData();
                        const res = await fetch(cfg.urls.stopAttendance, { method: 'POST', body: form });
                        const js = await res.json().catch(() => null);
                        
                        if (js && js.ok) {
                            console.log('[AUTO-END] Stop attendance successful, finalizing...');
                            
                            // Save seen students
                            try { saveSeen(seenThisSession, window.currentSessionId); } catch (e) { }
                            
                            // Update status
                            if (previewStatus) {
                                previewStatus.textContent = 'Đã kết thúc tự động';
                                previewStatus.classList.remove('text-success');
                                previewStatus.classList.add('text-muted');
                            }
                            
                            // Finalize attendance (create absent records)
                            if (cfg.urls && cfg.urls.finalizeAttendance && window.currentSessionId) {
                                try {
                                    const fd = new FormData();
                                    fd.append('session_id', String(window.currentSessionId));
                                    const f = await fetch(cfg.urls.finalizeAttendance, { method: 'POST', body: fd });
                                    const fj = await f.json().catch(() => null);
                                    console.log('[AUTO-END] Finalize response:', fj);
                                    
                                    // Clear session state
                                    seenThisSession = new Set();
                                    window.currentSessionId = null;
                                    
                                    // Redirect to sessions list
                                    if (cfg.urls && cfg.urls.sessionsList) {
                                        console.log('[AUTO-END] Redirecting to sessions list');
                                        window.location.href = cfg.urls.sessionsList;
                                        return;
                                    } else if (cfg.urls && cfg.urls.attendanceHistory) {
                                        console.log('[AUTO-END] Redirecting to attendance history');
                                        window.location.href = cfg.urls.attendanceHistory;
                                        return;
                                    }
                                } catch (e) { 
                                    console.warn('[AUTO-END] Finalize failed', e); 
                                }
                            }
                            
                            // Fallback redirect
                            if (cfg.urls && cfg.urls.sessionsList) {
                                window.location.href = cfg.urls.sessionsList;
                            }
                        } else {
                            console.error('[AUTO-END] Stop attendance failed:', js);
                            if (countdownEl) countdownEl.textContent = 'Lỗi tự động kết thúc';
                        }
                    } catch (e) { 
                        console.error('[AUTO-END] Failed:', e); 
                        if (countdownEl) countdownEl.textContent = 'Lỗi tự động kết thúc';
                    }
                })();
                return;
            }
            const s = Math.floor(diff / 1000) % 60;
            const m = Math.floor(diff / (1000 * 60)) % 60;
            const h = Math.floor(diff / (1000 * 60 * 60));
            if (countdownEl) {
                countdownEl.textContent = `Thời gian còn lại: ${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
            }
        }
        tick();
        countdownInterval = setInterval(tick, 1000);
    }

    function stopCountdown() {
        if (countdownInterval) { clearInterval(countdownInterval); countdownInterval = null; }
    }

    // Server-side attendance start/stop
    // helper: check model status and enable StartSrv accordingly
    async function checkModelStatus() {
        try {
            const res = await fetch((cfg && cfg.urls && cfg.urls.modelStatus) ? cfg.urls.modelStatus : '/teacher/api/model_status');
            const js = await res.json();
            const ready = js && js.ready;
            // Enable browser-start only when model is ready
            if (btnStartBrowser) btnStartBrowser.disabled = !ready;
            // also keep server-start button behavior consistent
            if (btnStartSrv) btnStartSrv.disabled = !ready;
            if (previewStatus) previewStatus.textContent = ready ? 'Model sẵn sàng' : 'Model chưa sẵn sàng';
            return ready;
        } catch (e) {
            console.debug('model status check failed', e);
            if (btnStartSrv) btnStartSrv.disabled = true;
            if (btnStartBrowser) btnStartBrowser.disabled = true;
            if (previewStatus) previewStatus.textContent = 'Model: lỗi';
            return false;
        }
    }

    // run initial check on load and poll periodically so the UI updates when model becomes ready
    try { checkModelStatus(); setInterval(checkModelStatus, 3000); } catch (e) { }

    btnStartSrv?.addEventListener('click', async () => {
        // If a preview is already running we will reuse the existing MediaStream
        // rather than opening a new one. This ensures preview + capture share the
        // same physical webcam and avoids device busy errors.
        try {
            if (!stream) {
                stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
                video.srcObject = stream;
                try { await video.play(); } catch (e) { console.debug('video.play failed (will rely on autoplay/user gesture)', e); }
            } else {
                // reuse running preview stream
                video.srcObject = stream;
                try { await video.play(); } catch (e) { console.debug('video.play reuse failed', e); }
            }
            if (btnStart) btnStart.disabled = true;
            // show preview Stop only when user explicitly requested preview; hide while server-side attendance runs
            if (btnStop) btnStop.disabled = false; btnStop.classList.remove('d-none');
            if (btnStartBrowser) btnStartBrowser.disabled = true;
            if (btnStopBrowser) btnStopBrowser.disabled = false;
        } catch (e) {
            console.error('Cannot open webcam', e);
            alert('Không thể mở webcam: ' + (e && e.message ? e.message : e));
            if (stream) { try { stream.getTracks().forEach(t => t.stop()); } catch (err) { console.debug('failed stopping stream after error', err); } stream = null; video.srcObject = null; }
        }

        if (!cfg.urls || !cfg.urls.startAttendance) return;
        try {
            // start session (no attendance_type)
            const form = new FormData();
            const res = await fetch(cfg.urls.startAttendance, { method: 'POST', body: form });
            const data = await res.json();
            // debug
            try { console.debug('startAttendance response', data); } catch (e) { }
            if (data.ok && data.session_id) {
                // server accepted and returned a session_id — now start sending frames
                if (previewStatus) { previewStatus.textContent = 'Đang chạy'; previewStatus.classList.remove('text-muted'); previewStatus.classList.add('text-success'); }
                if (btnStopSrv) btnStopSrv.disabled = false;
                // hide preview stop while server-side/browser-side attendance runs
                if (btnStop) { btnStop.classList.add('d-none'); btnStop.disabled = true; }
                // New session started: reset UI list and seen set so counts reflect this session
                window.currentSessionId = data.session_id;
                // remove persisted seen for this session and reset
                try { sessionStorage.removeItem(seenKey(data.session_id)); } catch (e) { }
                seenThisSession = new Set();
                const attendanceListEl = document.getElementById('attendanceList');
                if (attendanceListEl) attendanceListEl.innerHTML = '';
                const list = document.getElementById('attendanceList');
                let el = document.getElementById('sessionInfo');
                if (!el) {
                    el = document.createElement('div'); el.id = 'sessionInfo'; el.className = 'mb-2 small text-muted';
                    if (list && list.parentNode) list.parentNode.insertBefore(el, list);
                }
                el.textContent = `Buổi: ${data.session_number} · Khung: ${data.time_bucket}`;
                try {
                    const session = { id: data.session_id, session_number: data.session_number, time_bucket: data.time_bucket, session_open: data.session_open, ended_at: data.ended_at };
                    applySessionFlagsToUI(session);
                } catch (e) { console.debug('session flag handling failed', e); }
                // start browser capture loop only now
                try {
                    // reuse browserRealtime code path: simulate clicking browser start
                    if (!browserInterval) {
                        if (!stream) {
                            stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
                            video.srcObject = stream;
                            try { await video.play(); } catch (e) { console.debug('video.play failed before capture', e); }
                        }
                        // set canvas sizes to fixed capture size (downscale)
                        canvas.width = CAPTURE.width; canvas.height = CAPTURE.height;
                        let inflight = false;
                        browserInterval = setInterval(async () => {
                            if (inflight) return; inflight = true;
                            try {
                                ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                                const dataUrl = canvas.toDataURL('image/jpeg', CAPTURE.jpegQuality);
                                const form2 = new FormData();
                                form2.append('image', dataUrl);
                                const classId = (cfg && cfg.class_section_id) ? String(cfg.class_section_id) : (document.getElementById('class_section_id')?.value || '');
                                form2.append('class_id', classId);
                                if (window.currentSessionId) form2.append('attendance_session_id', window.currentSessionId);
                                form2.append('write', '1');
                                const recognizeUrl = (cfg && cfg.urls && cfg.urls.realtimeRecognize) ? cfg.urls.realtimeRecognize : '/api/realtime/recognize';
                                const r = await fetch(recognizeUrl, { method: 'POST', body: form2 });
                                // ignore result handling here; original browserRealtime code handles UI updates
                                const raw = await r.text().catch(() => '');
                                let js = null; try { js = raw ? JSON.parse(raw) : null; } catch (e) { js = null; }
                                if (!r.ok) { console.warn('recognize HTTP', r.status, raw); }
                            } catch (e) { console.warn('browser realtime failed', e); }
                            finally { inflight = false; }
                        }, CAPTURE.intervalMs);
                    }
                    // start countdown based on server-provided session_date/time_bucket
                    try {
                        const sessDate = (typeof data !== 'undefined' && data.session_date) ? data.session_date : null;
                        const end = getBucketEnd((typeof data !== 'undefined' && data.time_bucket) ? data.time_bucket : null, sessDate);
                        startCountdown(end);
                    } catch (e) { console.debug('start countdown failed', e); }
                } catch (e) { console.warn('failed to start browser capture after startAttendance', e); }
            } else {
                alert('Không thể bật: ' + (data.error || 'Lỗi không xác định'));
            }
        } catch (e) { alert('Lỗi: ' + e); }
    });

    btnStopSrv?.addEventListener('click', async () => {
        if (!cfg.urls || !cfg.urls.stopAttendance) return;
        btnStopSrv.disabled = true;
        try {
            // stop session on server
            const form = new FormData();
            const res = await fetch(cfg.urls.stopAttendance, { method: 'POST', body: form });
            const js = await res.json();
            if (js && js.ok) {
                if (previewStatus) { previewStatus.textContent = 'Đã dừng'; previewStatus.classList.remove('text-success'); previewStatus.classList.add('text-muted'); }
                // stop countdown when server stopped the session
                try { stopCountdown(); } catch (e) { }
                try { if (btnStartBrowser) btnStartBrowser.textContent = 'Bắt đầu (webcam)'; } catch (e) { }
                // server stop likely ended the session - update UI
                applySessionFlagsToUI({ session_open: false, ended_at: new Date().toISOString() });
                // stop any browser-side capture loop and clear current session so no more frames are sent
                try {
                    if (browserInterval) { clearInterval(browserInterval); browserInterval = null; }
                    if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
                    if (video) video.srcObject = null;
                } catch (e) { console.debug('failed to stop local capture after server stop', e); }
                // persist seen set then clear
                try { saveSeen(seenThisSession, window.currentSessionId); } catch (e) { }
                window.currentSessionId = null;
                seenThisSession = new Set();
                // show preview stop button again
                if (btnStop) { btnStop.classList.remove('d-none'); btnStop.disabled = true; }
            } else {
                alert('Không thể dừng: ' + (js.error || 'Lỗi'));
            }
        } catch (e) { console.warn('Stop server failed', e); }
        finally {
            btnStopSrv.disabled = false;
            if (cfg.urls && cfg.urls.finalizeAttendance) {
                try {
                    const f = await fetch(cfg.urls.finalizeAttendance, { method: 'POST' });
                    const fj = await f.json();
                    if (fj && fj.ok) {
                        // prefer redirecting to sessions list if available, otherwise attendance history
                        if (cfg.urls && cfg.urls.sessionsList) {
                            window.location.href = cfg.urls.sessionsList;
                        } else if (cfg.urls && cfg.urls.attendanceHistory) {
                            window.location.href = cfg.urls.attendanceHistory;
                        }
                    }
                } catch (e) { console.warn('Finalize failed', e); }
            }
        }
    });

    // End session button: force ending the attendance session (attendance_type='end')
    const btnEndSession = document.getElementById('btnEndSession');
    btnEndSession?.addEventListener('click', async () => {
        if (!cfg.urls || !cfg.urls.stopAttendance) return;
        try {
            btnEndSession.disabled = true;
            const form = new FormData();
            const res = await fetch(cfg.urls.stopAttendance, { method: 'POST', body: form });
            const js = await res.json();
            if (js && js.ok) {
                // stop any browser-side capture loop and clear current session
                if (browserInterval) { clearInterval(browserInterval); browserInterval = null; }
                if (stream) { try { stream.getTracks().forEach(t => t.stop()); } catch (e) { } stream = null; }
                if (video) video.srcObject = null;
                // mark UI as session ended
                try { applySessionFlagsToUI({ session_open: false, ended_at: new Date().toISOString() }); } catch (e) { }
                try { saveSeen(seenThisSession, window.currentSessionId); } catch (e) { }
                window.currentSessionId = null;
                seenThisSession = new Set();
                try { stopCountdown(); } catch (e) { }
                try { if (btnStartBrowser) btnStartBrowser.textContent = 'Bắt đầu (webcam)'; } catch (e) { }
                // navigate to history after finalize
                if (cfg.urls && cfg.urls.finalizeAttendance && window.currentSessionId) {
                    try {
                        const fd = new FormData();
                        fd.append('session_id', String(window.currentSessionId));
                        const f = await fetch(cfg.urls.finalizeAttendance, { method: 'POST', body: fd });
                        const fj = await f.json();
                        if (fj && fj.ok && cfg.urls.attendanceHistory) {
                            window.location.href = cfg.urls.attendanceHistory;
                        }
                    } catch (e) { console.warn('Finalize after end failed', e); }
                }
            } else {
                alert('Không thể kết thúc buổi: ' + (js && js.error ? js.error : 'Lỗi'));
            }
        } catch (e) { console.warn('End session failed', e); }
        finally { btnEndSession.disabled = false; }
    });

    async function fetchAttendance() {
        if (!cfg.urls || !cfg.urls.attendancePoll) return;
        try {
            // include session and attendance_type if available so UI shows session-scoped records
            let url = cfg.urls.attendancePoll;
            const params = new URLSearchParams();
            if (window.currentSessionId) params.set('session_id', String(window.currentSessionId));
            if (String(url).includes('?')) url = url + '&' + params.toString(); else url = url + (params.toString() ? '?' + params.toString() : '');
            const res = await fetch(url);
            const js = await res.json();
            if (js && js.ok) {
                const list = document.getElementById('attendanceList');
                if (!list) return;
                list.innerHTML = '';
                js.records.forEach(r => {
                    const li = document.createElement('li');
                    li.className = 'list-group-item';
                    li.textContent = `${r.student_name} (${r.student_mssv}) - ${new Date(r.timestamp).toLocaleTimeString()}`;
                    list.appendChild(li);
                });
                // update attended count UI
                try {
                    const cnt = js.records.length || 0;
                    const el = document.getElementById('attendedCount');
                    if (el) el.textContent = String(cnt);
                } catch (e) { console.debug('failed to update attendedCount', e); }
            }
        } catch (e) { console.warn('attendance poll failed', e); }
    }

    let pollInterval = null;
    function startPolling() { if (pollInterval) return; fetchAttendance(); pollInterval = setInterval(fetchAttendance, 2000); }
    function stopPolling() { if (pollInterval) { clearInterval(pollInterval); pollInterval = null; } }

    // local preview handlers
    btnStart?.addEventListener('click', startLocalPreview);
    btnStop?.addEventListener('click', stopLocalPreview);

    // browser realtime handlers
    btnStartBrowser?.addEventListener('click', async () => {
        // Ensure a session is open on the server before sending frames so
        // attendance_session_id is always attached and data goes to the new session.
        try {
            if (!window.currentSessionId && cfg.urls && cfg.urls.startAttendance) {
                try {
                    const form = new FormData();
                    const res = await fetch(cfg.urls.startAttendance, { method: 'POST', body: form });
                    const data = await res.json();
                    if (data && data.ok && data.session_id) {
                        window.currentSessionId = data.session_id;
                        // Reset UI for the new session
                        try { sessionStorage.removeItem(seenKey(data.session_id)); } catch (e) { }
                        seenThisSession = new Set();
                        const attendanceListEl = document.getElementById('attendanceList');
                        if (attendanceListEl) attendanceListEl.innerHTML = '';
                        let info = document.getElementById('sessionInfo');
                        const list = document.getElementById('attendanceList');
                        if (!info) { info = document.createElement('div'); info.id = 'sessionInfo'; info.className = 'mb-2 small text-muted'; if (list && list.parentNode) list.parentNode.insertBefore(info, list); }
                        info.textContent = `Buổi: ${data.session_number} · Khung: ${data.time_bucket}`;
                        try { applySessionFlagsToUI({ id: data.session_id, session_open: data.session_open, ended_at: data.ended_at }); } catch (e) { }
                        if (previewStatus) { previewStatus.textContent = 'Đang chạy'; previewStatus.classList.remove('text-muted'); previewStatus.classList.add('text-success'); }
                    } else {
                        alert('Không thể bắt đầu buổi mới: ' + (data && data.error ? data.error : 'Lỗi không xác định'));
                        return;
                    }
                } catch (e) {
                    console.warn('startAttendance failed before browser realtime', e);
                    alert('Không thể bắt đầu buổi mới');
                    return;
                }
            }

            // If a preview stream is already active, reuse it for browser realtime
            if (!stream) {
                stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
                video.srcObject = stream;
                try { await video.play(); } catch (e) { console.debug('video.play failed for browser realtime', e); }
            } else {
                // reuse existing preview stream
                video.srcObject = stream;
                try { await video.play(); } catch (e) { console.debug('video.play reuse failed for browser realtime', e); }
            }
            if (btnStart) btnStart.disabled = true;
            // hide the preview "Dừng" while attendance is running so teacher doesn't stop the preview
            if (btnStop) { btnStop.classList.add('d-none'); btnStop.disabled = true; }
            if (btnStartBrowser) btnStartBrowser.disabled = true;
            if (btnStopBrowser) btnStopBrowser.disabled = false;
            // use fixed capture size to reduce bandwidth & CPU
            canvas.width = CAPTURE.width; canvas.height = CAPTURE.height;

            if (!browserInterval) {
                let inflight = false;
                browserInterval = setInterval(async () => {
                    if (inflight) return; inflight = true;
                    try {
                        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                        const dataUrl = canvas.toDataURL('image/jpeg', CAPTURE.jpegQuality);
                        const form = new FormData();
                        form.append('image', dataUrl);
                        // prefer configured class_section_id; fallback to hidden input if template rendered one
                        const classId = (cfg && cfg.class_section_id) ? String(cfg.class_section_id) : (document.getElementById('class_section_id')?.value || '');
                        if (!classId) {
                            console.warn('take_attendance: class_id is empty — server /api/realtime/recognize requires class_id.');
                        }
                        form.append('class_id', classId);
                        if (window.currentSessionId) form.append('attendance_session_id', window.currentSessionId);
                        form.append('write', '1');
                        const recognizeUrl = (cfg && cfg.urls && cfg.urls.realtimeRecognize) ? cfg.urls.realtimeRecognize : '/api/realtime/recognize';
                        console.debug('take_attendance: posting to', recognizeUrl, 'class_id=', classId, 'session_id=', window.currentSessionId);
                        const res = await fetch(recognizeUrl, { method: 'POST', body: form });
                        const raw = await res.text().catch(() => '');
                        let js = null; try { js = raw ? JSON.parse(raw) : null; } catch (e) { js = null; }
                        if (!res.ok) {
                            console.error('take_attendance: recognize returned HTTP', res.status, 'raw:', raw, 'json:', js);
                            if (js && js.error) console.warn('Server error:', js.error);
                            return;
                        }

                        // clear overlay each frame
                        if (octx) octx.clearRect(0, 0, overlay.width || 0, overlay.height || 0);

                        if (js && js.ok) {
                            // prefer new multi-face format
                            const faces = Array.isArray(js.faces) ? js.faces : (js.detected ? [{ bbox: js.bbox || js.box || null, match: js.match || null }] : []);

                            // compute scale between captured canvas (capture size) and overlay (display size)
                            const scaleX = (overlay && overlay.width && canvas.width) ? (overlay.width / canvas.width) : 1;
                            const scaleY = (overlay && overlay.height && canvas.height) ? (overlay.height / canvas.height) : 1;

                            for (const f of faces) {
                                const bbox = f.bbox || null;
                                const match = f.match || null;
                                const tid = f.track_id || null;
                                if (!bbox || bbox.length < 4) continue;
                                let [x, y, w, h] = bbox;
                                // scale to overlay
                                x = Math.round(x * scaleX);
                                y = Math.round(y * scaleY);
                                w = Math.round(w * scaleX);
                                h = Math.round(h * scaleY);

                                try {
                                    if (octx) {
                                        // choose color by status: saved (green) > confirmed (yellow) > detected (blue)
                                        // determine display match: prefer current match; otherwise fall back to last known match for this track
                                        let displayMatch = match;
                                        if (!displayMatch && tid && lastMatchByTrack[tid]) {
                                            const entry = lastMatchByTrack[tid];
                                            if ((Date.now() - entry.ts) < MATCH_HOLD_MS) {
                                                displayMatch = { student_id: entry.student_id, name: entry.name, mssv: entry.mssv };
                                            }
                                        }
                                        let color = '#2196F3'; // blue default
                                        if (displayMatch) {
                                            if (f.db_saved) color = '#00C853';
                                            else if (f.confirmed) color = '#FFD54F';
                                            else color = '#2196F3';
                                        }
                                        octx.strokeStyle = color;
                                        octx.lineWidth = 3;
                                        octx.strokeRect(x, y, w, h);

                                        // label background
                                        const label = displayMatch ? `${displayMatch.name || ''} (${displayMatch.mssv || ''})` : 'Chưa xác định';
                                        octx.font = '16px sans-serif';
                                        const padding = 6;
                                        const textWidth = octx.measureText(label).width;
                                        const tx = x;
                                        const ty = Math.max(16, y - 6);
                                        octx.fillStyle = 'rgba(0,0,0,0.45)';
                                        octx.fillRect(tx - 2, ty - 16, textWidth + padding, 20);
                                        octx.fillStyle = '#ffffff';
                                        octx.fillText(label, tx + 2, ty);
                                    }
                                } catch (e) { console.warn('overlay draw failed for face', e); }

                                // update last-known match per track when we have a match
                                try {
                                    if (match && match.student_id && tid) {
                                        lastMatchByTrack[tid] = { student_id: match.student_id, name: match.name, mssv: match.mssv, ts: Date.now() };
                                    }
                                } catch (e) { }

                                // Only add to the attendance list once per student per session, and
                                // only after the server actually saved the record (db_saved=true).
                                if (match && match.student_id && f && f.db_saved && !seenThisSession.has(match.student_id)) {
                                    seenThisSession.add(match.student_id);
                                    try { saveSeen(seenThisSession, window.currentSessionId); } catch (e) { }
                                    const list = document.getElementById('attendanceList');
                                    const li = document.createElement('li');
                                    // apply contextual class for visual status
                                    let cls = 'list-group-item';
                                    if (f.db_saved) cls += ' list-group-item-success';
                                    else if (f.confirmed) cls += ' list-group-item-warning';
                                    li.className = cls;
                                    li.textContent = `${match.name} (${match.mssv}) - ${new Date().toLocaleTimeString()}`;
                                    console.debug('Adding attendance list item for', match.student_id, match.name, 'listExists=', !!list, 'db_saved=', f.db_saved, 'confirmed=', f.confirmed);
                                    if (list) list.insertBefore(li, list.firstChild);
                                    // increment attendedCount in the UI
                                    try {
                                        const el = document.getElementById('attendedCount');
                                        if (el) {
                                            const cur = parseInt(el.textContent || '0', 10) || 0;
                                            el.textContent = String(cur + 1);
                                        }
                                    } catch (e) { console.debug('failed to bump attendedCount', e); }
                                }
                            }
                        }
                    } catch (e) { console.warn('browser realtime failed', e); }
                    finally { inflight = false; }
                }, CAPTURE.intervalMs);
            }

        } catch (e) { alert('Không thể mở webcam: ' + e); }
    });

    btnStopBrowser?.addEventListener('click', async () => {
        // Kết thúc điểm danh: dừng camera, đóng session và chuyển đến danh sách buổi
        console.log('btnStopBrowser clicked, currentSessionId:', window.currentSessionId);
        if (!confirm('Đóng buổi điểm danh này? Không thể tiếp tục điểm danh sau khi kết thúc.')) return;

        // Disable all buttons immediately
        if (btnStopBrowser) btnStopBrowser.disabled = true;
        if (btnStartBrowser) btnStartBrowser.disabled = true;
        if (btnStart) btnStart.disabled = true;

        try {
            // Stop camera
            if (browserInterval) { clearInterval(browserInterval); browserInterval = null; }
            if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
            if (video) video.srcObject = null;
            if (octx) octx.clearRect(0, 0, overlay.width || 0, overlay.height || 0);

            // End session on server
            console.log('Calling stopAttendance API:', cfg.urls?.stopAttendance);
            if (cfg.urls && cfg.urls.stopAttendance) {
                const form = new FormData();
                const res = await fetch(cfg.urls.stopAttendance, { method: 'POST', body: form });
                console.log('stopAttendance response status:', res.status);
                const js = await res.json();
                console.log('stopAttendance response:', js);

                if (js && js.ok) {
                    // Save seen students
                    try { saveSeen(seenThisSession, window.currentSessionId); } catch (e) { }

                    // Clear UI state
                    seenThisSession = new Set();
                    try { stopCountdown(); } catch (e) { }

                    // Update status
                    if (previewStatus) {
                        previewStatus.textContent = 'Đã kết thúc';
                        previewStatus.classList.remove('text-success');
                        previewStatus.classList.add('text-muted');
                    }

                    // Finalize and redirect to sessions list
                    if (cfg.urls && cfg.urls.finalizeAttendance && window.currentSessionId) {
                        try {
                            const fd = new FormData();
                            fd.append('session_id', String(window.currentSessionId));
                            const f = await fetch(cfg.urls.finalizeAttendance, { method: 'POST', body: fd });
                            const fj = await f.json();
                            if (fj && fj.ok) {
                                // Redirect to sessions list
                                if (cfg.urls && cfg.urls.sessionsList) {
                                    window.location.href = cfg.urls.sessionsList;
                                    return;
                                } else if (cfg.urls && cfg.urls.attendanceHistory) {
                                    window.location.href = cfg.urls.attendanceHistory;
                                    return;
                                }
                            }
                        } catch (e) { console.warn('Finalize failed', e); }
                    }

                    // Fallback: redirect to sessions list anyway
                    if (cfg.urls && cfg.urls.sessionsList) {
                        window.location.href = cfg.urls.sessionsList;
                        return;
                    }
                } else {
                    // Error case: re-enable buttons
                    alert('Không thể kết thúc: ' + (js && js.error ? js.error : 'Lỗi'));
                    if (btnStopBrowser) btnStopBrowser.disabled = false;
                    if (btnStart) btnStart.disabled = false;
                    if (btnStartBrowser) btnStartBrowser.disabled = false;
                }
            }
        } catch (e) {
            console.warn('Stop browser failed', e);
            alert('Lỗi khi kết thúc: ' + e);
            // Error case: re-enable buttons
            if (btnStopBrowser) btnStopBrowser.disabled = false;
            if (btnStart) btnStart.disabled = false;
            if (btnStartBrowser) btnStartBrowser.disabled = false;
        }
    });



    // auto-start polling when script loads
    startPolling();

    // When teacher changes attendance type (start/end), refresh the polled list
    // and clear client-side seen set so UI reflects the selected type/session.
    try {
        const sel = document.getElementById('attendanceType');
        if (sel) {
            sel.addEventListener('change', () => {
                try {
                    // clear in-memory and persisted seen for the session
                    try { sessionStorage.removeItem(seenKey(window.currentSessionId)); } catch (e) { }
                    seenThisSession = loadSeen(window.currentSessionId);
                    const list = document.getElementById('attendanceList');
                    if (list) list.innerHTML = '';
                    // Immediately fetch attendance for the session
                    fetchAttendance();
                } catch (e) { console.debug('attendanceType change handler failed', e); }
            });
        }
    } catch (e) { console.debug('attendanceType init listener failed', e); }
})();

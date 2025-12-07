// Preview and register helper for register_face.html
(async function () {
    const INTERVAL = 250; // ms between detection frames

    function qs(id) { return document.getElementById(id); }
    const video = qs('preview');
    const overlay = qs('overlay');
    const faceStatus = qs('faceStatus');
    const btnStart = qs('btnStart');
    const btnStop = qs('btnStop');
    const btnStartSrv = qs('btnStartSrv');
    const btnStopSrv = qs('btnStopSrv');
    const srvStatus = qs('srvStatus');
    const srvResult = qs('srvResult');
    const countdownEl = qs('countdown');

    const cfg = window.PREVIEW_CONFIG || {
        previewDetectUrl: '/api/preview/detect',
        startUrl: '/student/start_register',
        statusUrl: '/student/register_status',
        stopUrl: '/student/stop_register'
    };

    let running = false;
    let stream = null;
    let serverRunning = false;
    let stopRequested = false;

    async function startCamera() {
        stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        video.srcObject = stream;
        await video.play();
        overlay.width = video.videoWidth || overlay.clientWidth || 640;
        overlay.height = video.videoHeight || overlay.clientHeight || 480;
        // new preview run; clear any previous stop request
        stopRequested = false;
    }

    // helper to visually enable/disable the Start Register button
    function setStartSrvEnabled(enabled) {
        if (!btnStartSrv) return;
        btnStartSrv.disabled = !enabled;
        if (!enabled) {
            btnStartSrv.style.opacity = '0.5';
            btnStartSrv.style.pointerEvents = 'none';
        } else {
            btnStartSrv.style.opacity = '';
            btnStartSrv.style.pointerEvents = '';
        }
    }

    // disable start by default until a face is detected
    try { setStartSrvEnabled(false); } catch (e) { }

    function resetToInitial() {
        try { if (stream) { stream.getTracks().forEach(t => t.stop()); } } catch (e) { console.warn('resetToInitial stop stream', e); }
        stream = null;
        if (video) { video.srcObject = null; }
        if (overlay) { const ctx = overlay.getContext('2d'); ctx && ctx.clearRect(0, 0, overlay.width || 0, overlay.height || 0); }
        running = false;
        if (btnStop) { btnStop.classList.add('d-none'); btnStop.disabled = true; }
        if (btnStart) { btnStart.disabled = false; }
        if (btnStartSrv) { setStartSrvEnabled(false); }
        if (btnStopSrv) { btnStopSrv.disabled = false; }
        if (faceStatus) { faceStatus.className = 'badge bg-secondary'; faceStatus.textContent = 'Chưa phát hiện khuôn mặt'; }
        if (countdownEl) { countdownEl.classList.add('d-none'); }
        if (srvResult) { srvResult.className = 'alert d-none'; srvResult.textContent = ''; }
        if (srvStatus) { srvStatus.textContent = 'Đã dừng'; srvStatus.classList.remove('text-success', 'text-danger', 'text-primary'); srvStatus.classList.add('text-muted'); }
        // ensure background loops/requests know we've stopped
        stopRequested = true;
    }

    // Reset only the preview/camera UI (keep server/upload status and messages intact)
    function resetPreviewOnly() {
        try { if (stream) { stream.getTracks().forEach(t => t.stop()); } } catch (e) { console.warn('resetPreviewOnly stop stream', e); }
        stream = null;
        if (video) { video.srcObject = null; }
        if (overlay) { const ctx = overlay.getContext('2d'); ctx && ctx.clearRect(0, 0, overlay.width || 0, overlay.height || 0); }
        running = false;
        // hide preview stop, enable preview start
        if (btnStop) { btnStop.classList.add('d-none'); btnStop.disabled = true; }
        if (btnStart) { btnStart.disabled = false; }
        // disable start server until new preview run or server action
        if (btnStartSrv) { setStartSrvEnabled(false); }
        // reset face preview status to neutral so UI doesn't stay 'ĐÃ NHẬN DIỆN'
        if (faceStatus) { faceStatus.className = 'badge bg-secondary'; faceStatus.textContent = 'Chưa phát hiện khuôn mặt'; }
        // leave srvStatus, srvResult, and countdown as-is (do not clear)
        // ensure background loops/requests know we've stopped
        stopRequested = true;
    }

    async function sendFrameAndDetect() {
        const temp = document.createElement('canvas');
        temp.width = video.videoWidth || overlay.width;
        temp.height = video.videoHeight || overlay.height;
        const tctx = temp.getContext('2d');
        tctx.drawImage(video, 0, 0, temp.width, temp.height);
        const dataURL = temp.toDataURL('image/jpeg', 0.7);
        try {
            if (stopRequested) return { detected: false };
            const form = new FormData();
            form.append('image', dataURL);
            const res = await fetch(cfg.previewDetectUrl, { method: 'POST', body: form });
            if (!res.ok) return { detected: false };
            if (stopRequested) return { detected: false };
            const js = await res.json();
            // normalize response shapes from server: {detected:true, bbox:[x,y,w,h]} or {bbox:...} or {boxes:[..]} etc.
            try {
                // quick debug when testing locally - will only appear in dev console
                console.debug && console.debug('preview.detect ->', js);
            } catch (e) { }
            let detected = false;
            let bbox = null;
            if (!js) return { detected: false };
            if (js.detected === true) detected = true;
            if (Array.isArray(js.bbox) && js.bbox.length === 4) { bbox = js.bbox; detected = true; }
            // common alt shape: boxes: [[x,y,w,h], ...]
            if (!detected && Array.isArray(js.boxes) && js.boxes.length > 0) { bbox = js.boxes[0]; detected = true; }
            // alt nested results
            if (!detected && Array.isArray(js.results) && js.results.length > 0) {
                const r0 = js.results[0];
                if (r0 && Array.isArray(r0.bbox) && r0.bbox.length === 4) { bbox = r0.bbox; detected = true; }
                else if (Array.isArray(r0) && r0.length === 4) { bbox = r0; detected = true; }
            }
            return { detected: !!detected, bbox: bbox };
        } catch (e) {
            console.error('preview detect error', e);
            return { detected: false };
        }
    }

    function drawBox(bbox) {
        const ctx = overlay.getContext('2d');
        ctx.clearRect(0, 0, overlay.width, overlay.height);
        if (!bbox) return;
        const [x, y, w, h] = bbox;
        ctx.strokeStyle = '#00FF00';
        ctx.lineWidth = 3;
        ctx.strokeRect(x, y, w, h);
    }

    async function loop() {
        const ctx = overlay.getContext('2d');
        while (running) {
            if (stopRequested) break;
            if (video.readyState < 2) { await new Promise(r => setTimeout(r, 100)); continue; }
            const res = await sendFrameAndDetect();
            if (stopRequested) break;
            // normalize detection flag from server
            const detectedFlag = !!(res && res.detected);
            // no on-page debug output
            // double-check stopRequested to avoid updating UI after user pressed stop
            if (stopRequested) { break; }
            if (detectedFlag) {
                if (faceStatus) { faceStatus.className = 'badge bg-success'; faceStatus.textContent = 'ĐÃ NHẬN DIỆN'; }
                drawBox(res.bbox);
                // only allow start when face is detected (visual & disabled state)
                setStartSrvEnabled(true);
            } else {
                if (faceStatus) { faceStatus.className = 'badge bg-secondary'; faceStatus.textContent = 'Chưa phát hiện khuôn mặt'; }
                ctx.clearRect(0, 0, overlay.width, overlay.height);
                setStartSrvEnabled(false);
            }
            await new Promise(r => setTimeout(r, INTERVAL));
        }
    }

    // Server start/poll handlers
    async function startServerRegister() {
        if (!cfg.startUrl) return;
        // disable to avoid double clicks
        setStartSrvEnabled(false);
        serverRunning = true;
        // prevent user from stopping during recording: disable stop buttons
        try { if (btnStopSrv) { btnStopSrv.disabled = true; btnStopSrv.style.opacity = '0.5'; btnStopSrv.style.pointerEvents = 'none'; } } catch (e) { }
        try { if (btnStop) { btnStop.classList.add('d-none'); btnStop.disabled = true; } } catch (e) { }
        try {
            // hide preview stop immediately when server recording begins (even if preview already running)
            if (btnStop) { btnStop.classList.add('d-none'); btnStop.disabled = true; }

            // call startUrl to keep compatibility and to get suggested duration
            const r0 = await fetch(cfg.startUrl, { method: 'POST' });
            let js0 = {};
            try { js0 = await r0.json(); } catch (e) { }
            // debug: log start_register response
            try { console.debug && console.debug('start_register response', js0); } catch (e) { }
            const duration = (js0 && js0.duration) ? parseInt(js0.duration, 10) : 10;

            if (srvStatus) {
                // process is recording
                srvStatus.textContent = 'đang ghi';
                // use primary color (blue) for active recording
                srvStatus.classList.remove('text-muted', 'text-danger', 'text-primary', 'text-success');
                srvStatus.classList.add('text-primary');
            }
            // show countdown
            if (countdownEl) { countdownEl.textContent = duration + 's'; countdownEl.classList.remove('d-none'); }

            // ensure preview running and give camera a short warm-up time
            try {
                if (!running) {
                    await startCamera();
                    // small warm-up so the first captured frame isn't blank
                    await new Promise(r => setTimeout(r, 500));
                    running = true;
                    // when starting server recording hide the preview Stop button (only show Stop for preview tests)
                    if (btnStop) { btnStop.classList.add('d-none'); btnStop.disabled = true; }
                    if (btnStart) { btnStart.disabled = true; }
                    // reset face preview status to neutral when server recording starts
                    if (faceStatus) { faceStatus.className = 'badge bg-secondary'; faceStatus.textContent = 'Chưa phát hiện khuôn mặt'; }
                    loop();
                }
            } catch (e) { console.warn('startServerRegister: preview start failed', e); }

            // capture frames client-side
            // capture frames for approximately `duration` seconds using elapsed time
            const frames = [];
            const captureInterval = 600; // ms between frames
            const endTime = Date.now() + duration * 1000;
            while (Date.now() < endTime) {
                const temp = document.createElement('canvas');
                temp.width = video.videoWidth || overlay.width;
                temp.height = video.videoHeight || overlay.height;
                const tctx = temp.getContext('2d');
                tctx.drawImage(video, 0, 0, temp.width, temp.height);
                const blob = await new Promise(res => temp.toBlob(res, 'image/jpeg', 0.95));
                frames.push(blob);
                // update countdown (ceil to show remaining seconds)
                if (countdownEl) { countdownEl.textContent = Math.max(0, Math.ceil((endTime - Date.now()) / 1000)) + 's'; }
                // wait until next capture or until end
                await new Promise(r => setTimeout(r, captureInterval));
            }

            // stop camera immediately so user sees camera stop at end of duration
            if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
            if (overlay) overlay.getContext('2d').clearRect(0, 0, overlay.width, overlay.height);
            running = false;
            if (btnStop) { btnStop.classList.add('d-none'); btnStop.disabled = true; }
            if (btnStart) { btnStart.disabled = false; }
            // update UI to indicate upload in progress
            if (srvStatus) {
                // uploading images to server
                srvStatus.textContent = 'đang tải ảnh lên';
                srvStatus.classList.remove('text-muted', 'text-danger', 'text-success');
                srvStatus.classList.add('text-primary');
            }

            // upload frames
            const form = new FormData();
            frames.forEach((b, idx) => form.append('images', b, `frame_${idx}.jpg`));
            const upResp = await fetch(cfg.uploadUrl || '/student/upload_register', { method: 'POST', body: form });
            const result = await upResp.json();
            // debug: log upload_register response
            try { console.debug && console.debug('upload_register response', upResp.status, result); } catch (e) { }
            
            // Handle error responses (400, 500, etc.)
            if (!upResp.ok) {
                // Server returned error status
                if (countdownEl) { countdownEl.classList.add('d-none'); }
                if (srvStatus) {
                    srvStatus.textContent = 'tải ảnh lên thất bại';
                    srvStatus.classList.remove('text-muted', 'text-primary', 'text-success');
                    srvStatus.classList.add('text-danger');
                }
                
                const errorType = (result && result.status && result.status.error_type) || 'unknown_error';
                const errorMessage = (result && result.status && result.status.message) || 'Đăng ký không thành công.';
                let errorIcon = 'fa-exclamation-triangle';
                let errorClass = 'alert-danger';
                
                if (errorType === 'duplicate_face') {
                    errorIcon = 'fa-copy';
                    errorClass = 'alert-danger';
                } else if (errorType === 'no_face_detected') {
                    errorIcon = 'fa-user-slash';
                } else if (errorType === 'insufficient_frames') {
                    errorIcon = 'fa-exclamation-circle';
                    errorClass = 'alert-warning';
                }
                
                srvResult.className = `alert ${errorClass}`;
                srvResult.innerHTML = `<i class="fas ${errorIcon}"></i> ${errorMessage}`;
                srvResult.classList.remove('d-none');
                
                if (result.flash && typeof result.flash === 'object') {
                    renderServerFlash(result.flash.message, result.flash.category || 'danger');
                }
                
                serverRunning = false;
                try { if (btnStopSrv) { btnStopSrv.disabled = false; btnStopSrv.style.opacity = ''; btnStopSrv.style.pointerEvents = ''; } } catch (e) { }
                resetPreviewOnly();
                try { setStartSrvEnabled(false); } catch (e) { }
                return;
            }
            // hide countdown
            if (countdownEl) { countdownEl.classList.add('d-none'); }
            // reset srvStatus class back to neutral (remove any previous color classes)
            if (srvStatus) { srvStatus.classList.remove('text-primary', 'text-danger', 'text-success'); srvStatus.classList.add('text-muted'); }
            if (result && result.ok && result.status) {
                const st = result.status;
                if (st.ok && st.saved) {
                    // upload + save successful
                    if (srvStatus) {
                        // upload + save successful
                        srvStatus.textContent = 'đã tải ảnh lên';
                        srvStatus.classList.remove('text-muted', 'text-primary', 'text-danger');
                        srvStatus.classList.add('text-success');
                    }
                    // show success message under instructions
                    srvResult.className = 'alert alert-success'; 
                    srvResult.innerHTML = '<i class="fas fa-check-circle"></i> ' + (st.message || 'Đăng ký khuôn mặt hoàn tất.');
                    // also render server-side flash payload if provided
                    if (result.flash && typeof result.flash === 'object') {
                        renderServerFlash(result.flash.message, result.flash.category || 'success');
                    }
                } else {
                    if (srvStatus) {
                        // upload failed
                        srvStatus.textContent = 'tải ảnh lên thất bại';
                        srvStatus.classList.remove('text-muted', 'text-primary', 'text-success');
                        srvStatus.classList.add('text-danger');
                    }
                    // Show detailed error message based on error type
                    const errorType = st.error_type || 'unknown_error';
                    let errorIcon = 'fa-exclamation-triangle';
                    let errorClass = 'alert-warning';
                    let errorMessage = st.message || 'Đăng ký không thành công.';
                    
                    // Map error types to specific icons and alert styles
                    if (errorType === 'no_face_detected') {
                        errorIcon = 'fa-user-slash';
                        errorClass = 'alert-danger';
                    } else if (errorType === 'camera_error') {
                        errorIcon = 'fa-video-slash';
                        errorClass = 'alert-danger';
                    } else if (errorType === 'insufficient_frames') {
                        errorIcon = 'fa-exclamation-circle';
                        errorClass = 'alert-warning';
                    } else if (errorType === 'duplicate_face') {
                        errorIcon = 'fa-copy';
                        errorClass = 'alert-danger';
                    } else if (errorType === 'database_error') {
                        errorIcon = 'fa-database';
                        errorClass = 'alert-danger';
                    } else if (errorType === 'system_error') {
                        errorIcon = 'fa-server';
                        errorClass = 'alert-danger';
                    }
                    
                    srvResult.className = `alert ${errorClass}`;
                    srvResult.innerHTML = `<i class="fas ${errorIcon}"></i> ${errorMessage}<br><small class="text-muted">Mã lỗi: ${errorType}</small>`;
                    
                    if (result.flash && typeof result.flash === 'object') {
                        renderServerFlash(result.flash.message, result.flash.category || 'warning');
                    }
                }
                srvResult.classList.remove('d-none');
            } else {
                if (srvStatus) {
                    srvStatus.textContent = 'tải ảnh lên thất bại';
                    srvStatus.classList.remove('text-muted', 'text-primary', 'text-success');
                    srvStatus.classList.add('text-danger');
                }
                const errorMsg = (result && result.error ? result.error : 'Không rõ');
                srvResult.className = 'alert alert-danger';
                srvResult.innerHTML = '<i class="fas fa-times-circle"></i> Upload thất bại: ' + errorMsg;
                srvResult.classList.remove('d-none');
            }
            // restore preview UI only; keep upload status/results visible for the user
            serverRunning = false;
            // re-enable server stop button after recording/upload complete so teacher can finalize or stop
            try { if (btnStopSrv) { btnStopSrv.disabled = false; btnStopSrv.style.opacity = ''; btnStopSrv.style.pointerEvents = ''; } } catch (e) { }
            // do not clear srvStatus or srvResult here so the upload progress/result remains visible
            resetPreviewOnly();
        } catch (e) {
            // on error ensure serverRunning flag is cleared and preview is stopped (but keep any status messages)
            serverRunning = false;
            try { resetPreviewOnly(); } catch (er) { /* best-effort */ }
            alert('Lỗi: ' + e);
        } finally {
            // keep Start disabled until a face is detected again (loop will re-enable)
            try { setStartSrvEnabled(false); } catch (e) { }
        }
    }

    async function stopServerRegister() {
        if (!cfg.stopUrl) return;
        btnStopSrv.disabled = true;
        try {
            const r = await fetch(cfg.stopUrl, { method: 'POST' });
            const js = await r.json();
            try { console.debug && console.debug('stop_register response', js); } catch (e) { }
            if (js && js.ok) {
                // stop confirmed by server: clear serverRunning and reset preview only
                serverRunning = false;
                // keep srvResult as-is; set srvStatus to 'Đã dừng'
                if (srvStatus) { srvStatus.textContent = 'Đã dừng'; srvStatus.classList.remove('text-success', 'text-danger'); srvStatus.classList.add('text-muted'); }
                resetPreviewOnly();
            } else {
                alert('Không thể dừng: ' + (js.error || 'Lỗi không xác định'));
            }
        } catch (e) {
            alert('Lỗi: ' + e);
        } finally {
            btnStopSrv.disabled = false;
        }
    }

    btnStart && btnStart.addEventListener('click', async () => {
        try {
            await startCamera();
            running = true;
            // reset preview face status so the loop will update to detected/undetected
            if (faceStatus) { faceStatus.className = 'badge bg-secondary'; faceStatus.textContent = 'Chưa phát hiện khuôn mặt'; }
            // show stop button (template may render it hidden)
            if (btnStop && !serverRunning) { btnStop.classList.remove('d-none'); btnStop.disabled = false; }
            btnStart.disabled = true;
            loop();
        } catch (e) {
            alert('Không thể mở webcam: ' + e);
        }
    });

    btnStop && btnStop.addEventListener('click', async () => {
        // If a server-side recording is active, request server stop; otherwise just reset preview
        if (serverRunning) {
            try {
                await stopServerRegister();
            } catch (e) {
                console.warn('stopServerRegister failed from preview stop', e);
                // fallback to reset
                resetPreviewOnly();
            }
        } else {
            // only reset preview UI, keep upload/status messages
            resetPreviewOnly();
        }
    });

    // When user clicks "Bắt đầu ghi", ensure camera/preview is running then start server-side capture
    btnStartSrv && btnStartSrv.addEventListener('click', async () => {
        // if preview not running, start it first
        try {
            if (!running) {
                await startCamera();
                running = true;
                if (btnStop) { btnStop.classList.remove('d-none'); btnStop.disabled = false; }
                if (btnStart) { btnStart.disabled = true; }
                loop();
            }
        } catch (e) {
            // if camera fails, still attempt to start server (server can capture if it has camera access)
            console.warn('Không thể mở webcam trước khi ghi, sẽ cố gắng bắt đầu ghi trên server:', e);
        }
        // start server-side capture (the server will auto-stop after its duration)
        await startServerRegister();
    });
    btnStopSrv && btnStopSrv.addEventListener('click', stopServerRegister);

})();

// helper to render a server flash message into the serverFlashContainer
function renderServerFlash(message, category) {
    try {
        const container = document.getElementById('serverFlashContainer');
        if (!container) return;
        // create alert element similar to _flash.html
        const div = document.createElement('div');
        div.className = `alert alert-${category} alert-dismissible fade show`;
        div.setAttribute('role', 'alert');
        div.textContent = message || '';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn-close';
        btn.setAttribute('data-bs-dismiss', 'alert');
        btn.setAttribute('aria-label', 'Close');
        btn.addEventListener('click', () => div.remove());
        div.appendChild(btn);
        // insert at top so newest is visible
        container.insertBefore(div, container.firstChild);
        // auto-dismiss after 4s
        setTimeout(() => {
            try {
                if (window.bootstrap && bootstrap.Alert && typeof bootstrap.Alert.getOrCreateInstance === 'function') {
                    const inst = bootstrap.Alert.getOrCreateInstance(div);
                    inst.close();
                } else {
                    div.remove();
                }
            } catch (e) { try { div.remove(); } catch (er) { } }
        }, 4000);
    } catch (e) { console.warn('renderServerFlash failed', e); }
}
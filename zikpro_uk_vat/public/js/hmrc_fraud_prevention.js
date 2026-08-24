// Collect and send real-time client information
function updateClientInfo() {
    const now = new Date();
    
    const clientInfo = {
        // Gov-Client-Screens = the physical SCREEN; Gov-Client-Window-Size = the browser WINDOW.
        screen_width: Math.max(screen.width, 1),
        screen_height: Math.max(screen.height, 1),
        window_width: Math.max(window.innerWidth, 1),
        window_height: Math.max(window.innerHeight, 1),
        color_depth: Math.max(screen.colorDepth, 24),
        pixel_ratio: Math.max(window.devicePixelRatio || 1, 0.5),
        timezone_offset: now.getTimezoneOffset() / -60,
        timestamp: now.toISOString()
    };

    if (frappe.session && frappe.session.user && frappe.session.user !== "Guest") {
        frappe.call({
            method: 'zikpro_uk_vat.utils.update_client_info',
            args: {
                screen_width: clientInfo.screen_width,
                screen_height: clientInfo.screen_height,
                window_width: clientInfo.window_width,
                window_height: clientInfo.window_height,
                color_depth: clientInfo.color_depth,
                pixel_ratio: clientInfo.pixel_ratio,
                timezone_offset: clientInfo.timezone_offset
            },
            callback: function (r) {
                if (!r.exc) {
                    console.debug('✅ HMRC Client Info Updated', clientInfo);
                    console.log("Session user:", frappe.session.user);
                } else {
                    console.error('❌ Client Info Update Failed', r);
                    console.log("Session user:", frappe.session.user);
                }
            }
        });
    } else {
        console.warn("⚠️ User is Guest. Skipping client info update.");
        console.log("Session user:", frappe.session.user);
    }
}

// FPH-2 fix: on the desk, `frappe.after_ajax` fires during early boot while
// `frappe.session.user` is still undefined, so the old code hit the Guest branch
// and NEVER posted the real screen — the fraud header always fell back to the
// default. Poll until a real user is present, then capture exactly once.
function _fphUserReady() {
    return window.frappe && frappe.session && frappe.session.user &&
        frappe.session.user !== "Guest";
}

let _fphTries = 0;
let _fphSent = false;
function updateClientInfoWhenReady() {
    if (_fphSent) { return; }
    if (_fphUserReady()) {
        _fphSent = true;
        updateClientInfo();
        return;
    }
    if (_fphTries++ < 60) {          // retry for ~30s while the desk boots
        setTimeout(updateClientInfoWhenReady, 500);
    }
}

frappe.after_ajax(updateClientInfoWhenReady);
document.addEventListener('DOMContentLoaded', updateClientInfoWhenReady);


// window.addEventListener('resize', frappe.utils.throttle(updateClientInfo, 500));
// window.addEventListener('orientationchange', frappe.utils.throttle(updateClientInfo, 500));

window.addEventListener('resize', frappe.utils.throttle(() => {
    if (frappe.session.user !== "Guest") {
        updateClientInfo();
    }
}, 500));

window.addEventListener('orientationchange', frappe.utils.throttle(() => {
    if (frappe.session.user !== "Guest") {
        updateClientInfo();
    }
}, 500));

// setInterval(updateClientInfo, 300000);

setInterval(() => {
    if (frappe.session.user !== "Guest") {
        updateClientInfo();
    }
}, 300000);
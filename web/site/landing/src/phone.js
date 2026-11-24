// Phones get the public pages only (Bryan, 2026-11-23): the landing page and the demo form work
// on a phone; the product does not, and the sign-in page says to sign in on a computer.
//
// A phone is a touch-first device whose screen's short side is under 600 CSS px. Touch follows
// MDN's advice for mobile detection (navigator.maxTouchPoints, then the `pointer: coarse` media
// query; never the user agent). The screen's short side separates phones (320 to 440 px, either
// way up) from tablets (744 px and up: iPad mini) and is the device's, not the window's, so a
// narrow desktop window or a touchscreen laptop is never sent away.
//
// A classic script, loaded in <head> before the stylesheets so the page never shows the form
// first: it sets <html data-device="phone"> and exposes basisIsPhone(window) for the app's entry
// script (web/public-entry.js), which loads this same file.
(function (root) {
  'use strict';
  const PHONE_SHORT_SIDE = 600;
  function isPhone(w) {
    const nav = w.navigator || {};
    const touch = (nav.maxTouchPoints || 0) > 0
      || (typeof w.matchMedia === 'function' && w.matchMedia('(pointer: coarse)').matches);
    const s = w.screen || {};
    const short = Math.min(s.width || Infinity, s.height || Infinity);
    return touch && short < PHONE_SHORT_SIDE;
  }
  root.basisIsPhone = isPhone;
  if (root.document && isPhone(root)) root.document.documentElement.dataset.device = 'phone';
})(self);

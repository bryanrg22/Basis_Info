/**
 * Application Constants
 * 
 * Centralized configuration for all magic numbers, strings, and constants.
 * This makes the codebase easier to maintain and modify.
 */

// Polling Configuration
export const POLLING_INTERVAL = 10000; // 10 seconds
export const MAX_POLLING_TIME = 5 * 60 * 1000; // 5 minutes

// Progress Animation Configuration
// Loading screens should feel responsive even while backend work finishes.
// These knobs are tuned so the animation advances smoothly but never hits 100%
// until the real data arrives, preventing "stuck at 100%" confusion.
export const PROGRESS_UPDATE_INTERVAL = 100; // milliseconds
export const PROGRESS_INCREMENT = 0.5; // percentage per update
export const PROGRESS_MAX_BEFORE_COMPLETE = 95; // cap at 95% until data is ready
export const MESSAGE_ROTATION_INTERVAL = 1200; // milliseconds

// Timeout Configuration
// These values determine how long we wait for Firestore and autosave responses
// before surfacing errors to the user. Short timeouts keep the UI from getting
// stuck when a network/index issue occurs.
export const STUDIES_LOAD_TIMEOUT = 10000; // 10 seconds
export const AUTO_SAVE_DEBOUNCE = 500; // milliseconds
export const CONFLICT_RESOLUTION_WINDOW = 2000; // milliseconds

// File Upload Configuration
// Only allow file types our downstream classifiers can process and cap size to
// prevent runaway storage costs or timeouts on slow networks.
export const MAX_FILE_SIZE = 100 * 1024 * 1024; // 100 MB
export const ALLOWED_FILE_TYPES = [
  'application/pdf',
  'image/jpeg',
  'image/png',
  'image/webp',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
];

// Statistics Calculation
export const REVENUE_MULTIPLIER = 0.05; // 5% of total assets
export const TAX_SAVINGS_MULTIPLIER = 0.03; // 3% of total assets

// Local Storage Keys
export const STORAGE_KEYS = {
  USER_PHOTO_URL: 'user_photoURL',
  USER_PHOTO_URL_UID: 'user_photoURL_uid',
} as const;

// Animation Durations
export const ANIMATION_DURATION = {
  FAST: 150,
  NORMAL: 300,
  SLOW: 500,
} as const;


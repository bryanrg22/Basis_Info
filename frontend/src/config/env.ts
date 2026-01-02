/**
 * Environment Variable Configuration
 *
 * Reads configuration from environment variables for production use.
 */

// Firebase Configuration from environment
export const FIREBASE_CONFIG = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY || '',
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN || '',
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID || '',
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET || '',
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID || '',
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID || '',
} as const;

// Application configuration
export const APP_CONFIG = {
  isDevelopment: process.env.NODE_ENV === 'development',
  isProduction: process.env.NODE_ENV === 'production',
  logLevel: process.env.NODE_ENV === 'development' ? 'debug' : 'info',
  appEnv: process.env.NEXT_PUBLIC_APP_ENV || 'development',
} as const;

// Backend API Configuration
export const API_CONFIG = {
  backendUrl: process.env.NEXT_PUBLIC_BACKEND_API_URL || 'http://localhost:8000',
} as const;

// Check if Firebase is properly configured
export function isFirebaseConfigured(): boolean {
  return Boolean(
    FIREBASE_CONFIG.apiKey &&
    FIREBASE_CONFIG.apiKey !== 'YOUR_API_KEY_HERE' &&
    FIREBASE_CONFIG.projectId
  );
}

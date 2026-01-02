/**
 * Firebase Configuration and Initialization
 *
 * Initializes Firebase app, Auth, Firestore, and Storage for the frontend.
 */

import { initializeApp, getApps, FirebaseApp } from 'firebase/app';
import { getAuth, Auth } from 'firebase/auth';
import { getFirestore, Firestore } from 'firebase/firestore';
import { getStorage, FirebaseStorage } from 'firebase/storage';

// Firebase configuration from environment variables
const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
};

// Initialize Firebase only once
let app: FirebaseApp;
let auth: Auth;
let firestore: Firestore;
let storage: FirebaseStorage;

// Check if Firebase is configured
const isConfigured = firebaseConfig.apiKey &&
  firebaseConfig.apiKey !== 'YOUR_API_KEY_HERE' &&
  firebaseConfig.projectId;

if (isConfigured) {
  // Initialize Firebase app (reuse existing if already initialized)
  if (getApps().length === 0) {
    app = initializeApp(firebaseConfig);
  } else {
    app = getApps()[0];
  }

  // Initialize Firebase services
  auth = getAuth(app);
  firestore = getFirestore(app);
  storage = getStorage(app);
} else {
  // Log warning in development
  if (typeof window !== 'undefined' && process.env.NODE_ENV === 'development') {
    console.warn(
      'Firebase is not configured. Please add your Firebase config to .env.local.\n' +
      'Get your config from Firebase Console → Project Settings → Your apps → Web app'
    );
  }
}

// Export Firebase services
export { app, auth, firestore, storage, isConfigured };

// Export config check helper
export function checkFirebaseConfig(): boolean {
  if (!isConfigured) {
    throw new Error(
      'Firebase is not configured. Please add your Firebase config to frontend/.env.local'
    );
  }
  return true;
}

/**
 * Backend Facade Layer
 *
 * Re-exports all backend services from a single abstraction point.
 * Uses real Firebase services for authentication, Firestore, and storage.
 */

// Re-export Firebase services
export { auth, firestore, storage, isConfigured } from '../firebase';

// Re-export storage service
export { storageService } from '../storage';

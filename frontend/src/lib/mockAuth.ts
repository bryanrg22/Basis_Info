/**
 * Mock Authentication Service
 * Provides mock authentication that always returns a logged-in demo user
 */

import demoConfig from '../../demo.config.json';

// Mock user object matching Firebase User interface
export interface MockUser {
  uid: string;
  email: string;
  displayName: string;
  photoURL: string | null;
}

// Get demo user from config
const getDemoUser = (): MockUser => {
  return {
    uid: demoConfig.demoUser.uid,
    email: demoConfig.demoUser.email,
    displayName: demoConfig.demoUser.displayName,
    photoURL: demoConfig.demoUser.photoURL,
  };
};

// Always return logged-in state
let currentUser: MockUser | null = getDemoUser();

/**
 * Get current user (always logged in for demo)
 */
export function getCurrentUser(): MockUser | null {
  return currentUser;
}

/**
 * Login (no-op for demo - always logged in)
 */
export async function login(): Promise<void> {
  // No-op - user is always logged in
  currentUser = getDemoUser();
}

/**
 * Logout (no-op for demo)
 */
export async function logout(): Promise<void> {
  // No-op - in demo mode, we stay logged in
  // But we can reset to demo user if needed
  currentUser = getDemoUser();
}

/**
 * Check if user is authenticated (always true for demo)
 */
export function isAuthenticated(): boolean {
  return currentUser !== null;
}

/**
 * Mock auth object matching Firebase Auth interface
 */
export const mockAuth = {
  currentUser: currentUser,
  onAuthStateChanged: (callback: (user: MockUser | null) => void) => {
    // Immediately call with current user
    callback(currentUser);
    
    // Return unsubscribe function (no-op)
    return () => {};
  },
};


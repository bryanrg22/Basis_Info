/**
 * Engineering Takeoff Local Storage Utility
 * 
 * Provides browser-only persistence for engineering takeoff state.
 * Stores asset and takeoff states per study, allowing engineers to
 * resume their work across page refreshes on the same device.
 */

import { AssetDemo, AssetTakeoffDemo } from '@/types/asset-takeoff.types';

/** Storage key pattern for engineering takeoffs */
const STORAGE_KEY_PREFIX = 'engineering_takeoffs';

/** Get the storage key for a study */
function getStorageKey(studyId: string): string {
  return `${STORAGE_KEY_PREFIX}:${studyId}`;
}

/** Shape of persisted state for a single asset */
export interface PersistedAssetState {
  asset: AssetDemo;
  takeoff: AssetTakeoffDemo;
  lastUpdated: string; // ISO timestamp
}

/** Shape of persisted state for a study (map of assetId -> state) */
export type PersistedStudyState = Record<string, PersistedAssetState>;

/**
 * Check if localStorage is available (guards against SSR and private mode)
 */
function isLocalStorageAvailable(): boolean {
  if (typeof window === 'undefined') return false;
  
  try {
    const testKey = '__storage_test__';
    window.localStorage.setItem(testKey, testKey);
    window.localStorage.removeItem(testKey);
    return true;
  } catch {
    return false;
  }
}

/**
 * Load all persisted takeoff state for a study.
 * Returns null if no state exists or localStorage is unavailable.
 */
export function loadTakeoffState(studyId: string): PersistedStudyState | null {
  if (!isLocalStorageAvailable()) return null;
  
  try {
    const key = getStorageKey(studyId);
    const stored = window.localStorage.getItem(key);
    if (!stored) return null;
    
    const parsed = JSON.parse(stored) as PersistedStudyState;
    return parsed;
  } catch (error) {
    console.warn('[takeoff-local-storage] Failed to load state:', error);
    return null;
  }
}

/**
 * Load persisted state for a specific asset.
 * Returns null if no state exists for the asset.
 */
export function loadAssetState(studyId: string, assetId: string): PersistedAssetState | null {
  const studyState = loadTakeoffState(studyId);
  if (!studyState) return null;
  
  return studyState[assetId] ?? null;
}

/**
 * Save state for a specific asset within a study.
 * Merges with existing state for other assets.
 */
export function saveAssetState(
  studyId: string,
  assetId: string,
  asset: AssetDemo,
  takeoff: AssetTakeoffDemo
): boolean {
  if (!isLocalStorageAvailable()) return false;
  
  try {
    const key = getStorageKey(studyId);
    const existingState = loadTakeoffState(studyId) ?? {};
    
    const updatedState: PersistedStudyState = {
      ...existingState,
      [assetId]: {
        asset,
        takeoff,
        lastUpdated: new Date().toISOString(),
      },
    };
    
    window.localStorage.setItem(key, JSON.stringify(updatedState));
    return true;
  } catch (error) {
    console.warn('[takeoff-local-storage] Failed to save state:', error);
    return false;
  }
}

/**
 * Save the entire study state at once.
 * Replaces any existing state.
 */
export function saveTakeoffState(studyId: string, state: PersistedStudyState): boolean {
  if (!isLocalStorageAvailable()) return false;
  
  try {
    const key = getStorageKey(studyId);
    window.localStorage.setItem(key, JSON.stringify(state));
    return true;
  } catch (error) {
    console.warn('[takeoff-local-storage] Failed to save study state:', error);
    return false;
  }
}

/**
 * Reset/clear all persisted state for a study.
 * Returns true if successful or if no state existed.
 */
export function resetTakeoffState(studyId: string): boolean {
  if (!isLocalStorageAvailable()) return true;
  
  try {
    const key = getStorageKey(studyId);
    window.localStorage.removeItem(key);
    return true;
  } catch (error) {
    console.warn('[takeoff-local-storage] Failed to reset state:', error);
    return false;
  }
}

/**
 * Get all asset IDs that have persisted state for a study.
 */
export function getPersistedAssetIds(studyId: string): string[] {
  const state = loadTakeoffState(studyId);
  if (!state) return [];
  return Object.keys(state);
}

/**
 * Check if any persisted state exists for a study.
 */
export function hasPersistedState(studyId: string): boolean {
  const state = loadTakeoffState(studyId);
  return state !== null && Object.keys(state).length > 0;
}


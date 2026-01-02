/**
 * Validation Utilities
 * 
 * Pure functions for validating data.
 */

import { Asset, Takeoff } from '@/types';

/**
 * Validates that a value is not null or undefined
 */
export function isNotNull<T>(value: T | null | undefined): value is T {
  return value !== null && value !== undefined;
}

/**
 * Validates that a value is a valid string
 */
export function isValidString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

/**
 * Validates that a value is a valid number
 */
export function isValidNumber(value: unknown): value is number {
  return typeof value === 'number' && !isNaN(value) && isFinite(value);
}

/**
 * Validates an asset object
 */
export function isValidAsset(value: unknown): value is Asset {
  if (!value || typeof value !== 'object') {
    return false;
  }
  
  const asset = value as Partial<Asset>;
  
  return (
    isValidString(asset.id) &&
    isValidString(asset.name) &&
    isValidString(asset.description) &&
    ['5-year', '15-year', '27.5-year'].includes(asset.category || '') &&
    isValidNumber(asset.estimatedValue) &&
    isValidNumber(asset.depreciationPeriod) &&
    isValidNumber(asset.percentageOfTotal) &&
    typeof asset.verified === 'boolean'
  );
}

/**
 * Validates a takeoff object
 */
export function isValidTakeoff(value: unknown): value is Takeoff {
  if (!value || typeof value !== 'object') {
    return false;
  }
  
  const takeoff = value as Partial<Takeoff>;
  
  return (
    isValidString(takeoff.id) &&
    isValidString(takeoff.description) &&
    isValidNumber(takeoff.quantity)
  );
}

/**
 * Type guard for getting a value from an object by key
 */
export function getFieldValue(
  obj: Record<string, unknown>,
  key: string
): unknown {
  return obj[key];
}


/**
 * Centralized Error Handling
 * 
 * Provides consistent error handling and logging throughout the application.
 */

import { logger } from '@/lib/logger';
import { AppError } from './error-types';

/**
 * Handles an error and returns a user-friendly message
 */
export function handleError(error: unknown): string {
  // Log the error
  if (error instanceof AppError) {
    logger.error(`[${error.code}] ${error.message}`, { 
      code: error.code, 
      statusCode: error.statusCode,
      field: 'field' in error ? error.field : undefined,
      cause: error.cause 
    });
    
    // Return user-friendly message
    return error.message;
  }
  
  if (error instanceof Error) {
    logger.error('Unexpected error', { error: error.message, stack: error.stack });
    return error.message || 'An unexpected error occurred';
  }
  
  logger.error('Unknown error', { error });
  return 'An unexpected error occurred';
}

/**
 * Wraps an async function with error handling
 */
export function withErrorHandling<T extends (...args: unknown[]) => Promise<unknown>>(
  fn: T,
  errorMessage?: string
): T {
  return (async (...args: Parameters<T>) => {
    try {
      return await fn(...args);
    } catch (error) {
      const message = errorMessage || handleError(error);
      throw new AppError(message, 'FUNCTION_ERROR', 500, error instanceof Error ? error : undefined);
    }
  }) as T;
}

/**
 * Converts a Firebase error to an AppError
 */
export function convertFirebaseError(error: unknown): AppError {
  if (error instanceof Error) {
    // Check for common Firebase error patterns
    if (error.message.includes('permission-denied')) {
      return new AppError('You do not have permission to perform this action', 'PERMISSION_DENIED', 403, error);
    }
    if (error.message.includes('not-found')) {
      return new AppError('The requested resource was not found', 'NOT_FOUND', 404, error);
    }
    if (error.message.includes('unauthenticated')) {
      return new AppError('You must be authenticated to perform this action', 'UNAUTHENTICATED', 401, error);
    }
    if (error.message.includes('network')) {
      return new AppError('Network error. Please check your connection and try again', 'NETWORK_ERROR', 503, error);
    }
    
    return new AppError(error.message, 'FIREBASE_ERROR', 500, error);
  }
  
  return new AppError('An unknown error occurred', 'UNKNOWN_ERROR', 500);
}


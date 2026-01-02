/**
 * Error Type Definitions
 * 
 * Custom error types for the application.
 */

/**
 * Base application error
 */
export class AppError extends Error {
  constructor(
    message: string,
    public code: string,
    public statusCode: number = 500,
    public cause?: Error
  ) {
    super(message);
    this.name = 'AppError';
    // Maintains proper stack trace for where our error was thrown (only available on V8)
    if (Error.captureStackTrace) {
      Error.captureStackTrace(this, AppError);
    }
  }
}

/**
 * Firestore-related errors
 */
export class FirestoreError extends AppError {
  constructor(message: string, cause?: Error) {
    super(message, 'FIRESTORE_ERROR', 500, cause);
    this.name = 'FirestoreError';
  }
}

/**
 * Storage-related errors
 */
export class StorageError extends AppError {
  constructor(message: string, cause?: Error) {
    super(message, 'STORAGE_ERROR', 500, cause);
    this.name = 'StorageError';
  }
}

/**
 * Authentication errors
 */
export class AuthError extends AppError {
  constructor(message: string, cause?: Error) {
    super(message, 'AUTH_ERROR', 401, cause);
    this.name = 'AuthError';
  }
}

/**
 * Validation errors
 */
export class ValidationError extends AppError {
  constructor(
    message: string,
    public field?: string,
    cause?: Error
  ) {
    super(message, 'VALIDATION_ERROR', 400, cause);
    this.name = 'ValidationError';
  }
}

/**
 * Not found errors
 */
export class NotFoundError extends AppError {
  constructor(message: string, cause?: Error) {
    super(message, 'NOT_FOUND', 404, cause);
    this.name = 'NotFoundError';
  }
}


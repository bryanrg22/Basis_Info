/**
 * Compatibility layer for Firebase types
 * Provides mock replacements for Firebase Firestore types
 */

/**
 * Mock Timestamp class that matches Firestore Timestamp interface
 */
export class MockTimestamp {
  private _seconds: number;
  private _nanoseconds: number;

  constructor(seconds: number = Date.now() / 1000, nanoseconds: number = 0) {
    this._seconds = Math.floor(seconds);
    this._nanoseconds = nanoseconds;
  }

  get seconds(): number {
    return this._seconds;
  }

  get nanoseconds(): number {
    return this._nanoseconds;
  }

  toDate(): Date {
    return new Date(this._seconds * 1000);
  }

  toMillis(): number {
    return this._seconds * 1000;
  }

  isEqual(other: MockTimestamp): boolean {
    return this._seconds === other._seconds && this._nanoseconds === other._nanoseconds;
  }

  valueOf(): string {
    return this.toString();
  }

  toString(): string {
    return `Timestamp(seconds=${this._seconds}, nanoseconds=${this._nanoseconds})`;
  }

  static now(): MockTimestamp {
    return new MockTimestamp(Date.now() / 1000, 0);
  }

  static fromDate(date: Date): MockTimestamp {
    return new MockTimestamp(date.getTime() / 1000, 0);
  }

  static fromMillis(milliseconds: number): MockTimestamp {
    return new MockTimestamp(milliseconds / 1000, 0);
  }
}

// Re-export as Timestamp for compatibility
export const Timestamp = MockTimestamp;

/**
 * Unsubscribe function type (matches Firestore unsubscribe)
 */
export type Unsubscribe = () => void;


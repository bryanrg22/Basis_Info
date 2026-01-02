/**
 * Logger Module
 *
 * Provides structured logging with levels.
 */

type LogLevel = 'debug' | 'info' | 'warn' | 'error';

const LOG_LEVELS: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
};

class Logger {
  private currentLevel: LogLevel;

  constructor() {
    const envLevel = (process.env.NEXT_PUBLIC_LOG_LEVEL || 'debug') as LogLevel;
    this.currentLevel = envLevel;
  }

  private shouldLog(level: LogLevel): boolean {
    return LOG_LEVELS[level] >= LOG_LEVELS[this.currentLevel];
  }

  private formatMessage(level: LogLevel, message: string, ...args: unknown[]): void {
    if (!this.shouldLog(level)) {
      return;
    }

    const timestamp = new Date().toISOString();
    const prefix = `[${timestamp}] [${level.toUpperCase()}]`;

    switch (level) {
      case 'debug':
        console.debug(prefix, message, ...args);
        break;
      case 'info':
        console.info(prefix, message, ...args);
        break;
      case 'warn':
        console.warn(prefix, message, ...args);
        break;
      case 'error':
        console.error(prefix, message, ...args);
        break;
    }
  }

  debug(message: string, ...args: unknown[]): void {
    this.formatMessage('debug', message, ...args);
  }

  info(message: string, ...args: unknown[]): void {
    this.formatMessage('info', message, ...args);
  }

  warn(message: string, ...args: unknown[]): void {
    this.formatMessage('warn', message, ...args);
  }

  error(message: string, ...args: unknown[]): void {
    this.formatMessage('error', message, ...args);
  }

  /**
   * Creates a scoped logger with a prefix for easier filtering
   */
  scope(prefix: string): ScopedLogger {
    return new ScopedLogger(this, prefix);
  }
}

class ScopedLogger {
  constructor(
    private logger: Logger,
    private prefix: string
  ) {}

  debug(message: string, ...args: unknown[]): void {
    this.logger.debug(`[${this.prefix}] ${message}`, ...args);
  }

  info(message: string, ...args: unknown[]): void {
    this.logger.info(`[${this.prefix}] ${message}`, ...args);
  }

  warn(message: string, ...args: unknown[]): void {
    this.logger.warn(`[${this.prefix}] ${message}`, ...args);
  }

  error(message: string, ...args: unknown[]): void {
    this.logger.error(`[${this.prefix}] ${message}`, ...args);
  }
}

// Export singleton instance
export const logger = new Logger();

// Export types for use in other files
export type { LogLevel };


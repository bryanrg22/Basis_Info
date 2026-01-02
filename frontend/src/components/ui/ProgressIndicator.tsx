/**
 * Progress Indicator Component
 * 
 * Reusable progress bar with animated progress and rotating messages.
 */

'use client';

interface ProgressIndicatorProps {
  progress: number;
  currentMessage: string;
  title: string;
  description: string;
  error?: string | null;
}

/**
 * Progress indicator with animated bar and rotating messages
 */
export default function ProgressIndicator({
  progress,
  currentMessage,
  title,
  description,
  error,
}: ProgressIndicatorProps) {
  return (
    <div className="p-6 max-w-2xl mx-auto">
      <div className="bg-white p-8 rounded-lg shadow-sm border border-gray-200 text-center">
        <div className="mb-6">
          <div className="w-16 h-16 bg-primary-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg
              className="w-8 h-8 text-primary-600 animate-spin"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
          </div>
          <h2 className="text-xl font-semibold text-gray-900 mb-2">{title}</h2>
          <p className="text-sm text-gray-600">{description}</p>
        </div>

        {/* Progress Bar */}
        <div className="w-full bg-gray-200 rounded-full h-2 mb-4">
          <div
            className="bg-primary-600 h-2 rounded-full transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>

        {/* Status Messages */}
        <div className="mb-2">
          <p className="text-sm text-primary-600 font-medium">{currentMessage}</p>
        </div>

        <p className="text-sm text-gray-500">{Math.round(progress)}% Complete</p>

        {/* Error Message */}
        {error && (
          <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg">
            <p className="text-sm text-red-600">{error}</p>
          </div>
        )}
      </div>
    </div>
  );
}


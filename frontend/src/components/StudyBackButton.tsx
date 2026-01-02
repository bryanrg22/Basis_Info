/**
 * Study Back Button Component
 * 
 * Displays a back arrow button that navigates to the previous workflow step.
 * Only shows if there is a previous step available.
 */

'use client';

import { useParams } from 'next/navigation';
import { useStudyNavigation } from '@/hooks/useStudyNavigation';

interface StudyBackButtonProps {
  className?: string;
}

export default function StudyBackButton({ className = '' }: StudyBackButtonProps) {
  const params = useParams();
  const studyId = params.id as string;
  
  const { hasPreviousStep, goToPreviousStep } = useStudyNavigation({ studyId });

  if (!hasPreviousStep()) {
    return null;
  }

  return (
    <button
      onClick={() => {
        goToPreviousStep().catch(err => {
          console.error('Error navigating to previous step:', err);
        });
      }}
      className={`flex items-center gap-2 text-gray-600 hover:text-gray-900 transition-colors ${className}`}
      aria-label="Go to previous step"
    >
      <svg
        className="w-5 h-5"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M15 19l-7-7 7-7"
        />
      </svg>
      <span className="text-sm font-medium">Back</span>
    </button>
  );
}


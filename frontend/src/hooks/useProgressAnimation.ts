/**
 * useProgressAnimation Hook
 * 
 * Manages progress bar animation and message rotation.
 */

import { useState, useEffect, useRef } from 'react';
import {
  PROGRESS_UPDATE_INTERVAL,
  PROGRESS_INCREMENT,
  PROGRESS_MAX_BEFORE_COMPLETE,
  MESSAGE_ROTATION_INTERVAL,
} from '@/config/constants';

interface UseProgressAnimationOptions {
  messages: string[];
  onComplete?: () => void;
}

/**
 * Manages progress animation and message rotation
 */
export function useProgressAnimation(
  options: UseProgressAnimationOptions
): {
  progress: number;
  currentMessage: string;
  setProgress: (progress: number) => void;
  complete: () => void;
} {
  const { messages, onComplete } = options;
  const [progress, setProgress] = useState(0);
  const [currentMessageIndex, setCurrentMessageIndex] = useState(0);
  const progressIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const messageIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Progress animation
  useEffect(() => {
    if (progress >= PROGRESS_MAX_BEFORE_COMPLETE) {
      if (progressIntervalRef.current) {
        clearInterval(progressIntervalRef.current);
        progressIntervalRef.current = null;
      }
      return;
    }

    progressIntervalRef.current = setInterval(() => {
      setProgress((prev) => {
        const next = Math.min(prev + PROGRESS_INCREMENT, PROGRESS_MAX_BEFORE_COMPLETE);
        return next;
      });
    }, PROGRESS_UPDATE_INTERVAL);

    return () => {
      if (progressIntervalRef.current) {
        clearInterval(progressIntervalRef.current);
      }
    };
  }, [progress]);

  // Message rotation
  useEffect(() => {
    if (messages.length === 0) {
      return;
    }

    messageIntervalRef.current = setInterval(() => {
      setCurrentMessageIndex((prev) => (prev + 1) % messages.length);
    }, MESSAGE_ROTATION_INTERVAL);

    return () => {
      if (messageIntervalRef.current) {
        clearInterval(messageIntervalRef.current);
      }
    };
  }, [messages.length]);

  const complete = () => {
    if (progressIntervalRef.current) {
      clearInterval(progressIntervalRef.current);
      progressIntervalRef.current = null;
    }
    if (messageIntervalRef.current) {
      clearInterval(messageIntervalRef.current);
      messageIntervalRef.current = null;
    }
    setProgress(100);
    if (onComplete) {
      onComplete();
    }
  };

  return {
    progress,
    currentMessage: messages[currentMessageIndex] || '',
    setProgress,
    complete,
  };
}


'use client';

import { useEffect, useRef } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import { useApp } from '@/contexts/AppContext';

/**
 * UserSync component
 *
 * Syncs the authenticated user to the AppContext on load.
 */
export function UserSync() {
  const { currentUser } = useAuth();
  const { dispatch } = useApp();
  const lastSyncedUidRef = useRef<string | null>(null);

  useEffect(() => {
    const currentUid = currentUser?.uid || null;
    if (currentUid === lastSyncedUidRef.current) {
      return;
    }

    if (currentUser) {
      // Sync authenticated user to AppContext
      dispatch({
        type: 'SET_USER',
        payload: {
          name: currentUser.displayName || 'User',
          email: currentUser.email || '',
          company: '',
          photoURL: currentUser.photoURL || null,
          uid: currentUser.uid,
        },
      });
      
      lastSyncedUidRef.current = currentUser.uid;
    } else {
      if (lastSyncedUidRef.current !== null) {
        dispatch({
          type: 'SET_USER',
          payload: {
            name: '',
            email: '',
            company: '',
            photoURL: null,
            uid: undefined,
          },
        });
        lastSyncedUidRef.current = null;
      }
    }
  }, [currentUser?.uid, currentUser, dispatch]);

  return null;
}

'use client';

import { useMemo, memo } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import { useApp } from '@/contexts/AppContext';
import LogoutButton from './LogoutButton';

// Memoized profile image component to prevent unnecessary re-renders
const ProfileImage = memo(({ photoURL, name }: { photoURL: string; name: string }) => {
  return (
    <img
      key={photoURL}
      src={photoURL}
      alt={name}
      width={32}
      height={32}
      className="w-8 h-8 rounded-full"
      loading="lazy"
      // Browser will cache based on URL - no need for additional cache headers
    />
  );
});
ProfileImage.displayName = 'ProfileImage';

function Header() {
  const { currentUser } = useAuth();
  const { state } = useApp();
  
  // Memoize the photoURL to prevent unnecessary re-renders
  const photoURL = useMemo(() => state.user.photoURL, [state.user.photoURL]);

  return (
    <header className="bg-white border-b border-gray-200 px-6 py-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
        </div>
        <div className="flex items-center gap-4">
          {currentUser && (
            <>
              <div className="flex items-center gap-3">
                {photoURL ? (
                  <ProfileImage photoURL={photoURL} name={state.user.name} />
                ) : (
                  <div className="w-8 h-8 bg-primary-500 rounded-full flex items-center justify-center text-white text-sm font-medium">
                    {state.user.name.split(' ').map(n => n[0]).join('') || 'U'}
                  </div>
                )}
                <div className="text-sm">
                  <p className="font-medium text-gray-900">{state.user.name || 'User'}</p>
                  <p className="text-gray-500 text-xs">{state.user.email}</p>
                </div>
              </div>
              <LogoutButton />
            </>
          )}
        </div>
      </div>
    </header>
  );
}

export default memo(Header);



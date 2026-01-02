'use client';

import { useApp } from '@/contexts/AppContext';
import Image from 'next/image';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

const navigation = [
  { name: 'Dashboard', href: '/dashboard', shortName: 'Dash' },
  { name: 'New Study', href: '/study/new', shortName: 'New' },
  { name: 'Settings', href: '/settings', shortName: 'Set' },
];

export default function Sidebar() {
  const { state, dispatch } = useApp();
  const pathname = usePathname();
  const isOpen = state.sidebarOpen;

  return (
    <div
      className={`${isOpen ? 'w-56' : 'w-14'} transition-all duration-200 ease-out bg-white border-r border-gray-200 flex flex-col`}
    >
      {/* Logo */}
      <div
        className={`${isOpen ? 'px-5' : 'px-2'} h-16 border-b border-gray-100 flex items-center ${isOpen ? '' : 'justify-center'}`}
      >
        <Image
          src={isOpen ? '/images/basis_logo.png' : '/images/basis_logo_no_words.png'}
          alt="Basis"
          width={isOpen ? 100 : 28}
          height={isOpen ? 28 : 28}
          className="transition-all duration-200"
        />
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4" aria-label="Main navigation">
        <ul className="space-y-1 px-2">
          {navigation.map((item) => {
            const isActive = pathname === item.href || pathname.startsWith(item.href + '/');
            return (
              <li key={item.name}>
                <Link
                  href={item.href}
                  title={!isOpen ? item.name : undefined}
                  aria-current={isActive ? 'page' : undefined}
                  className={`
                    relative flex items-center py-2 text-[13px] font-medium rounded-md transition-colors duration-150
                    focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:ring-gray-400
                    ${isOpen ? 'px-3' : 'justify-center px-0'}
                    ${
                      isActive
                        ? 'bg-gray-100 text-gray-900'
                        : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                    }
                  `}
                >
                  {/* Left accent bar for active item */}
                  {isActive && (
                    <span
                      className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-4 bg-gray-800 rounded-r"
                      aria-hidden="true"
                    />
                  )}
                  <span className={isOpen ? '' : 'text-[11px]'}>
                    {isOpen ? item.name : item.shortName}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Bottom Section with Toggle Button */}
      <div className="px-2 py-3 border-t border-gray-100">
        <div className={`flex items-center ${isOpen ? 'gap-3' : 'flex-col gap-2'}`}>
          {/* User avatar */}
          <div
            className="w-8 h-8 bg-gray-800 rounded-full flex items-center justify-center text-white text-xs font-medium shrink-0"
            title={state.user.name}
          >
            {state.user.name
              .split(' ')
              .map((n) => n[0])
              .join('')}
          </div>

          {isOpen && (
            <span className="flex-1 text-[13px] text-gray-700 font-medium truncate">
              {state.user.name}
            </span>
          )}

          {/* Collapse / expand toggle */}
          <button
            onClick={() => dispatch({ type: 'SET_SIDEBAR_OPEN', payload: !isOpen })}
            aria-label={isOpen ? 'Collapse sidebar' : 'Expand sidebar'}
            className="p-1.5 rounded-md text-gray-500 hover:bg-gray-100 hover:text-gray-700 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-400"
          >
            <svg
              className={`w-4 h-4 transition-transform duration-200 ${isOpen ? '' : 'rotate-180'}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}

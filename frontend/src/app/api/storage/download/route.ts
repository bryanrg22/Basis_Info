import { NextRequest, NextResponse } from 'next/server';

/**
 * Mock storage download route
 * 
 * In demo mode, this just returns a placeholder response.
 */
export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const path = searchParams.get('path');

  if (!path) {
    return NextResponse.json({ error: 'Path is required' }, { status: 400 });
  }

  // Return a redirect to the placeholder image
  return NextResponse.redirect(new URL('/images/placeholder-room.svg', request.url));
}


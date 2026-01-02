# Basis Cost Segregation Frontend

Next.js 14 frontend for the Basis Cost Segregation platform. This application provides an intuitive interface for engineers to manage cost segregation studies with AI-powered analysis.

## Tech Stack

- **Framework**: Next.js 14 (App Router)
- **Language**: TypeScript
- **Styling**: Tailwind CSS
- **Authentication**: Firebase Auth (Google OAuth)
- **Database**: Firebase Firestore
- **File Storage**: Firebase Storage
- **State Management**: React Context + real-time Firestore subscriptions

## Quick Start

```bash
# Install dependencies
npm install

# Set up environment variables
cp .env.example .env.local
# Edit .env.local with your Firebase config

# Run development server
npm run dev

# Build for production
npm run build

# Start production server
npm start
```

The app runs on **http://localhost:3000** by default.

## Environment Variables

Create a `.env.local` file with the following:

```env
# Firebase Configuration
NEXT_PUBLIC_FIREBASE_API_KEY=your-api-key
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=your-project.firebaseapp.com
NEXT_PUBLIC_FIREBASE_PROJECT_ID=your-project-id
NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=your-project.appspot.com
NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=your-sender-id
NEXT_PUBLIC_FIREBASE_APP_ID=your-app-id

# Backend API
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
```

## Project Structure

```
src/
├── app/                    # Next.js App Router pages
│   ├── (auth)/            # Authentication pages
│   ├── dashboard/         # Main dashboard
│   └── study/             # Study workflow pages
│       └── [id]/
│           ├── upload/    # File upload step
│           └── review/    # Review stages
├── api/                   # API layer
│   ├── firestore/         # Firestore CRUD operations
│   ├── storage/           # Firebase Storage operations
│   └── workflow.api.ts    # Backend workflow API
├── components/            # React components
│   ├── engineering-takeoff/
│   ├── asset-verification/
│   └── ui/               # Shared UI components
├── contexts/              # React Context providers
│   ├── AuthContext.tsx   # Authentication state
│   └── AppContext.tsx    # Application state
├── services/              # Business logic layer
├── types/                 # TypeScript type definitions
├── lib/                   # Utilities and configuration
│   ├── firebase.ts       # Firebase initialization
│   └── logger.ts         # Logging utilities
└── config/               # Application configuration
```

## Workflow Stages

The application guides users through a multi-stage cost segregation workflow:

| Stage | Route | Description |
|-------|-------|-------------|
| **Upload** | `/study/[id]/upload` | Upload property photos and documents |
| **Analyzing** | Auto | AI analyzes images with GPT-4 Vision |
| **Resource Extraction** | Auto | Backend extracts rooms and objects |
| **Review Rooms** | `/study/[id]/review/first` | Engineer reviews AI-detected rooms |
| **Engineering Takeoff** | `/study/[id]/review/resources` | Verify assets and quantities |
| **Completed** | `/study/[id]/review/resources` | Final review and export |

## Key Features

### Real-time Updates
Studies use Firestore real-time subscriptions for instant UI updates when the backend processes data.

### File Upload
- Drag-and-drop file upload
- Progress tracking
- Files stored in Firebase Storage with real download URLs
- Supports images (JPEG, PNG, WebP) and PDFs

### AI-Powered Analysis
- Images are analyzed by GPT-4 Vision via the backend
- Automatic room type detection
- Object/asset identification
- Confidence scores for all detections

### Engineer Review
- Approve or reject AI classifications
- Add/edit/delete rooms and assets
- Associate documents with assets
- Quantity verification with unit costs

## API Integration

The frontend communicates with the FastAPI backend for AI processing:

```typescript
// Start workflow analysis
const response = await workflowApi.startWorkflow({
  study_id: studyId,
  study_doc_ids: fileIds,
});

// Check workflow status
const status = await workflowApi.getStatus(studyId);
```

Backend endpoint: `POST /api/v1/workflow/start`

## Development

### Running with Backend

1. Start the backend (from `backEnd/` directory):
   ```bash
   cd ../backEnd
   poetry run uvicorn agentic.api.main:app --reload --port 8000
   ```

2. Start the frontend:
   ```bash
   npm run dev
   ```

### Type Checking

```bash
npm run type-check
```

### Linting

```bash
npm run lint
```

## Firebase Setup

1. Create a Firebase project at [console.firebase.google.com](https://console.firebase.google.com)
2. Enable Authentication (Google provider)
3. Create a Firestore database
4. Create a Storage bucket
5. Add your web app and copy the config to `.env.local`

### Firestore Rules

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /studies/{studyId} {
      allow read, write: if request.auth != null
        && request.auth.uid == resource.data.userId;
      allow create: if request.auth != null;
    }
  }
}
```

### Storage Rules

```javascript
rules_version = '2';
service firebase.storage {
  match /b/{bucket}/o {
    match /studies/{studyId}/{allPaths=**} {
      allow read, write: if request.auth != null;
    }
  }
}
```

## Troubleshooting

### Firebase not configured
Ensure all `NEXT_PUBLIC_FIREBASE_*` environment variables are set in `.env.local`.

### Backend connection failed
Verify the backend is running on the port specified in `NEXT_PUBLIC_BACKEND_URL`.

### Images not loading
Check Firebase Storage rules allow authenticated read access.

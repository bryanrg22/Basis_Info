'use client';

import { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useApp } from '@/contexts/AppContext';
import ProtectedRoute from '@/components/ProtectedRoute';
import Header from '@/components/Header';
import Sidebar from '@/components/Sidebar';
import StudyBackButton from '@/components/StudyBackButton';
import {
  AppraisalResources,
  ResourceChecklistItem,
  ResourceChecklistStatus,
  ResourceSectionId,
} from '@/types';

type InteriorMechanicalSnapshot = {
  heating?: { type?: string[]; fuel?: string };
  cooling?: string;
  fireplaces?: { count?: number; type?: string };
  rooms_above_grade?: { total_rooms: number; bedrooms: number; bathrooms: number };
  gross_living_area_above_grade_sqft?: number;
};

/**
 * Resource Extraction Review Page
 *
 * Checklist-driven step where an engineer reviews the extracted
 * appraisal resources (subject, neighborhood, site, improvements,
 * sales grid, cost approach, photos & sketch) before moving on
 * to room categorization.
 */
export default function ResourceExtractionReviewPage() {
  const { state, updateWorkflowStatus, updateStudyInFirestore } = useApp();
  const params = useParams();
  const router = useRouter();
  const studyId = params.id as string;

  const study = state.studies.find((s) => s.id === studyId);

  const [resources, setResources] = useState<AppraisalResources | null>(null);
  const [checklist, setChecklist] = useState<ResourceChecklistItem[]>([]);
  const [selectedSection, setSelectedSection] =
    useState<ResourceSectionId>('subject');
  const [isContinuing, setIsContinuing] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  
  // Debounce timer for saving checklist state
  const saveTimerRef = useRef<NodeJS.Timeout | null>(null);

  // Load resources and guard navigation based on workflow status.
  useEffect(() => {
    if (!study) {
      router.push('/dashboard');
      return;
    }

    // Initialize currentStep and visitedSteps if not present
    const currentStep = study.currentStep || study.workflowStatus;
    const visitedSteps = study.visitedSteps || [study.workflowStatus];
    
    // If currentStep is not resource_extraction, but it's in visitedSteps, allow access
    // Otherwise, only allow if workflowStatus is resource_extraction (first time)
    const canAccess = 
      currentStep === 'resource_extraction' || 
      visitedSteps.includes('resource_extraction') ||
      study.workflowStatus === 'resource_extraction';

    if (!canAccess) {
      // Redirect to appropriate page based on current step
      const targetStep = currentStep;
      if (targetStep === 'uploading_documents' || targetStep === 'analyzing_rooms') {
        router.push(`/study/${studyId}/analyze/first`);
      } else if (targetStep === 'reviewing_rooms') {
        router.push(`/study/${studyId}/review/first`);
      } else if (targetStep === 'engineering_takeoff') {
        router.push(`/study/${studyId}/engineering-takeoff`);
      } else if (targetStep === 'completed') {
        router.push(`/study/${studyId}/complete`);
      } else {
        router.push('/dashboard');
      }
      return;
    }

    // Update currentStep if needed
    if (currentStep !== 'resource_extraction') {
      const updatedVisitedSteps = visitedSteps.includes('resource_extraction') 
        ? visitedSteps 
        : [...visitedSteps, 'resource_extraction'];
      
      updateStudyInFirestore(studyId, {
        currentStep: 'resource_extraction',
        visitedSteps: updatedVisitedSteps,
      }).catch(err => console.error('Failed to update current step:', err));
    }

    // Load resources from study (populated by backend workflow)
    setIsLoading(true);
    setLoadError(null);
    try {
      // Use real appraisal resources from study (populated by backend)
      const data = study.appraisalResources;

      if (!data) {
        setLoadError('No appraisal resources found. Please ensure documents have been analyzed.');
        setIsLoading(false);
        return;
      }

      // Create default checklist items
      const defaultChecklist: ResourceChecklistItem[] = [
        { id: 'subject', sectionId: 'subject', title: 'Subject Property', status: 'NOT_STARTED' },
        { id: 'neighborhood', sectionId: 'neighborhood', title: 'Neighborhood', status: 'NOT_STARTED' },
        { id: 'site', sectionId: 'site', title: 'Site', status: 'NOT_STARTED' },
        { id: 'improvements', sectionId: 'improvements', title: 'Improvements', status: 'NOT_STARTED' },
        { id: 'sales_comparison', sectionId: 'sales_comparison', title: 'Sales Comparison', status: 'NOT_STARTED' },
        { id: 'cost_approach', sectionId: 'cost_approach', title: 'Cost Approach', status: 'NOT_STARTED' },
        { id: 'photos_sketch', sectionId: 'photos_sketch', title: 'Photos & Sketch', status: 'NOT_STARTED' },
        { id: 'overall', sectionId: 'overall', title: 'Overall Review', status: 'NOT_STARTED' },
      ];

      // Load saved checklist state if available
      let items = defaultChecklist;
      if (study.resourceChecklist) {
        items = items.map(item => {
          const savedStatus = study.resourceChecklist![item.id];
          if (savedStatus !== undefined) {
            return {
              ...item,
              status: savedStatus ? 'VERIFIED' : 'NOT_STARTED' as ResourceChecklistStatus,
            };
          }
          return item;
        });
      }

      setResources(data);
      setChecklist(items);
    } catch (error) {
      console.error('Failed to load appraisal resources', error);
      setLoadError('Unable to load appraisal resources for this study. Please try again in a moment.');
    } finally {
      setIsLoading(false);
    }
  }, [study, studyId, router, updateStudyInFirestore]);

  // Cleanup save timer on unmount
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
      }
    };
  }, []);

  const sectionOrder: ResourceSectionId[] = useMemo(
    () => [
      'subject',
      'neighborhood',
      'site',
      'improvements',
      'sales_comparison',
      'cost_approach',
      'photos_sketch',
      'overall',
    ],
    [],
  );

  const sectionLabels: Record<ResourceSectionId, string> = {
    subject: 'Subject & Assignment',
    neighborhood: 'Neighborhood & Market',
    site: 'Site & Zoning',
    improvements: 'Improvements',
    sales_comparison: 'Sales Comparison',
    cost_approach: 'Cost Approach',
    photos_sketch: 'Photos & Sketch',
    overall: 'Overall Readiness',
  };

  const cycleStatus = (current: ResourceChecklistStatus): ResourceChecklistStatus => {
    // Simplified interaction: toggle directly between Not started and Verified.
    if (current === 'NOT_STARTED') return 'VERIFIED';
    return 'NOT_STARTED';
  };

  // Save checklist state to study
  const saveChecklistState = useCallback((items: ResourceChecklistItem[]) => {
    if (!study) return;
    
    // Clear existing timer
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
    }
    
    // Debounce save
    saveTimerRef.current = setTimeout(() => {
      const checklistState: Record<string, boolean> = {};
      items.forEach(item => {
        checklistState[item.id] = item.status === 'VERIFIED';
      });
      
      updateStudyInFirestore(studyId, {
        resourceChecklist: checklistState,
      }).catch(err => console.error('Failed to save checklist state:', err));
    }, 500);
  }, [study, studyId, updateStudyInFirestore]);

  const handleToggleChecklistItem = (id: string) => {
    setChecklist((prev) => {
      const updated = prev.map((item) =>
        item.id === id ? { ...item, status: cycleStatus(item.status) } : item,
      );

      // If all non-overall items are VERIFIED, mark overall as VERIFIED.
      const nonOverallComplete = updated
        .filter((item) => item.sectionId !== 'overall')
        .every((item) => item.status === 'VERIFIED');

      const final = updated.map((item) =>
        item.sectionId === 'overall'
          ? {
              ...item,
              status: nonOverallComplete ? 'VERIFIED' : item.status,
            }
          : item,
      );
      
      // Save state
      saveChecklistState(final);
      
      return final;
    });
  };

  const overallProgress = useMemo(() => {
    if (!checklist.length) return { verified: 0, total: 0, percent: 0 };
    const total = checklist.length;
    const verified = checklist.filter((i) => i.status === 'VERIFIED').length;
    const percent = Math.round((verified / total) * 100);
    return { verified, total, percent };
  }, [checklist]);

  const allVerified = useMemo(
    () =>
      checklist.length > 0 &&
      checklist.every((item) => item.status === 'VERIFIED'),
    [checklist],
  );

  const sectionStatus = useCallback(
    (sectionId: ResourceSectionId): ResourceChecklistStatus => {
      const items = checklist.filter((i) => i.sectionId === sectionId);
      if (!items.length) return 'NOT_STARTED';
      if (items.every((i) => i.status === 'VERIFIED')) return 'VERIFIED';
      if (items.some((i) => i.status === 'IN_REVIEW' || i.status === 'VERIFIED')) {
        return 'IN_REVIEW';
      }
      return 'NOT_STARTED';
    },
    [checklist],
  );

  const statusBadgeClasses = (status: ResourceChecklistStatus): string => {
    if (status === 'VERIFIED') {
      return 'bg-primary-100 text-primary-700 border-primary-200';
    }
    if (status === 'IN_REVIEW') {
      return 'bg-rose-100 text-rose-700 border-rose-200';
    }
    return 'bg-slate-100 text-slate-600 border-slate-200';
  };

  const handleContinue = async () => {
    if (!study) return;
    if (!allVerified) {
      alert(
        'Please verify all checklist items before continuing to Room Categorization.',
      );
      return;
    }

    setIsContinuing(true);
    try {
      await updateWorkflowStatus(studyId, 'reviewing_rooms');
      router.push(`/study/${studyId}/review/first`);
    } catch (error) {
      console.error('Error continuing to room review:', error);
      alert('Failed to continue. Please try again.');
    } finally {
      setIsContinuing(false);
    }
  };

  if (!study) {
    return (
      <ProtectedRoute>
        <div className="flex h-screen">
          <Sidebar />
          <div className="flex-1 flex flex-col overflow-hidden">
            <Header />
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center space-y-2">
                <div className="animate-spin h-8 w-8 border-2 border-primary-600 border-t-transparent rounded-full mx-auto" />
                <p className="text-gray-600 text-sm">Loading study…</p>
              </div>
            </div>
          </div>
        </div>
      </ProtectedRoute>
    );
  }

  if (isLoading || !resources) {
    return (
      <ProtectedRoute>
        <div className="flex h-screen">
          <Sidebar />
          <div className="flex-1 flex flex-col overflow-hidden">
            <Header />
            <main
              className="flex-1 overflow-y-auto"
              aria-busy="true"
              aria-live="polite"
              aria-label="Loading appraisal resources"
            >
              <div className="p-6 max-w-7xl mx-auto animate-pulse space-y-6">
                <div className="flex flex-col gap-3">
                  <div className="h-3 w-20 rounded-full bg-gray-200" />
                  <div className="h-6 w-64 rounded-full bg-gray-200" />
                  <div className="h-4 w-full max-w-2xl rounded-full bg-gray-200" />
                </div>
                <div className="grid gap-6 lg:grid-cols-4">
                  <div className="h-64 rounded-2xl border border-gray-200 bg-white" />
                  <div className="h-64 rounded-2xl border border-gray-200 bg-white lg:col-span-2" />
                  <div className="h-64 rounded-2xl border border-gray-200 bg-white" />
                </div>
              </div>
            </main>
          </div>
        </div>
      </ProtectedRoute>
    );
  }

  if (loadError) {
    return (
      <ProtectedRoute>
        <div className="flex h-screen">
          <Sidebar />
          <div className="flex-1 flex flex-col overflow-hidden">
            <Header />
            <main className="flex-1 overflow-y-auto">
              <div className="p-6 max-w-3xl mx-auto">
                <div className="rounded-2xl border border-rose-200 bg-rose-50 p-6 shadow-sm">
                  <h1 className="text-lg font-semibold text-rose-900 mb-2">
                    We couldn’t load the appraisal resources
                  </h1>
                  <p className="text-sm text-rose-800">
                    {loadError}
                  </p>
                  <button
                    type="button"
                    onClick={() => router.refresh()}
                    className="mt-4 inline-flex items-center gap-2 rounded-lg bg-rose-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-rose-700"
                  >
                    <svg
                      className="h-4 w-4"
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
                    Try again
                  </button>
                </div>
              </div>
            </main>
          </div>
        </div>
      </ProtectedRoute>
    );
  }

  const sectionItems = checklist.filter(
    (item) => item.sectionId === selectedSection,
  );

  const subject = resources.subject;
  const listingInfo = resources.listing_and_contract;
  const heroStats = [
    {
      label: 'Contract Price',
      value: `$${listingInfo.contract_price.toLocaleString()}`,
      helper: `List ${listingInfo.original_list_price.toLocaleString()} • ${listingInfo.sale_type}`,
    },
    {
      label: 'Days on Market',
      value: `${listingInfo.days_on_market} days`,
      helper: listingInfo.contract_date,
    },
    {
      label: 'Property Rights',
      value: subject.property_rights_appraised,
      helper: subject.assignment_type,
    },
  ];

  return (
    <ProtectedRoute>
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Header />
          <div className="flex-1 overflow-y-auto">
            <div className="p-6 max-w-7xl mx-auto">
              {/* Back Button */}
              <div className="mb-4">
                <StudyBackButton />
              </div>
              {/* Header */}
              <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-primary-600">
                    Step 2 • Resource Extraction
                  </p>
                  <h1 className="mt-1 text-3xl font-bold text-gray-900">
                    Review Appraisal Resources
                  </h1>
                  <p className="mt-2 text-sm text-gray-600 max-w-2xl">
                    Double-check the appraisal insights before you organize rooms. We’ve highlighted
                    the essentials so you can scan quickly and move forward with confidence.
                  </p>
                </div>
                <div
                  className="flex flex-col items-start md:items-end gap-2"
                  role="status"
                  aria-live="polite"
                >
                  <span className="text-xs font-medium text-gray-600">
                    {overallProgress.verified} of {overallProgress.total} checks verified
                  </span>
                  <div className="w-64 bg-gray-200 rounded-full h-2 overflow-hidden">
                    <div
                      className="h-2 bg-primary-600 rounded-full transition-all duration-300"
                      style={{ width: `${overallProgress.percent}%` }}
                    />
                  </div>
                  <span className="text-[11px] text-gray-500">
                    {overallProgress.percent === 100
                      ? 'All checks are verified. You’re ready to continue.'
                      : 'Work through each section and mark each item verified.'}
                  </span>
                </div>
              </div>

              {/* Hero summary */}
              <div className="mb-8 grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
                <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <p className="text-xs font-medium text-gray-500">Property</p>
                      <p className="text-lg font-semibold text-gray-900">
                        {subject.property_address}, {subject.city}
                      </p>
                      <p className="text-sm text-gray-500">
                        Borrower {subject.borrower} · Client {subject.lender_client}
                      </p>
                    </div>
                    <div className="inline-flex items-center gap-2 rounded-full bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700">
                      <span className="h-2 w-2 rounded-full bg-primary-500" />
                      Appraisal data
                    </div>
                  </div>
                  <dl className="mt-4 grid gap-4 sm:grid-cols-3">
                    {heroStats.map((stat) => (
                      <div
                        key={stat.label}
                        className="rounded-xl bg-gray-50 px-4 py-3 border border-gray-100"
                      >
                        <dt className="text-xs font-medium text-gray-500">{stat.label}</dt>
                        <dd className="text-base font-semibold text-gray-900">{stat.value}</dd>
                        <p className="text-xs text-gray-500 mt-1">{stat.helper}</p>
                      </div>
                    ))}
                  </dl>
                </div>
                <div className="rounded-2xl border border-primary-100 bg-primary-50/70 p-5 shadow-sm">
                  <p className="text-xs font-semibold text-primary-700 uppercase tracking-wide">
                    At-a-glance
                  </p>
                  <ul className="mt-3 space-y-2 text-sm text-primary-900">
                    <li className="flex items-center gap-2">
                      <svg className="h-4 w-4 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      Contract docs reviewed ({listingInfo.contract_documents_reviewed.length})
                    </li>
                    <li className="flex items-center gap-2">
                      <svg className="h-4 w-4 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      MLS #{listingInfo.mls_number} · {listingInfo.days_on_market} DOM
                    </li>
                    <li className="flex items-center gap-2">
                      <svg className="h-4 w-4 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      Taxes: ${subject.real_estate_taxes.toLocaleString()} ({subject.tax_year})
                    </li>
                  </ul>
                  <p className="mt-4 text-xs text-primary-800">
                    Use the cards below to spot-check any numbers that feel off.
                  </p>
                </div>
              </div>

              {/* Main layout */}
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Center: Section details (now takes up more space) */}
                <section className="lg:col-span-2 space-y-4">
                  <div className="bg-white border border-gray-200 rounded-xl shadow-sm">
                    <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
                      <div>
                        <h2 className="text-sm font-semibold text-gray-900">
                          {sectionLabels[selectedSection]}
                        </h2>
                        <p className="mt-1 text-xs text-gray-500">
                          Review the extracted data and use the checklist to confirm it
                          looks reasonable.
                        </p>
                      </div>
                    </div>
                    <div className="p-5 space-y-4">
                      {renderSectionContent(selectedSection, resources)}
                    </div>
                  </div>

                  {/* Section-specific checklist */}
                  {sectionItems.length > 0 && (
                    <div className="bg-white border border-gray-200 rounded-xl shadow-sm">
                      <div className="px-5 py-4 border-b border-gray-100">
                          <h3 className="text-sm font-semibold text-gray-900">
                            {sectionLabels[selectedSection]} Checklist
                          </h3>
                        <p className="mt-1 text-xs text-gray-500">
                          Click an item to toggle between Not started and Verified.
                        </p>
                      </div>
                      <ul className="p-4 space-y-2">
                        {sectionItems.map((item) => (
                          <li key={item.id}>
                            <button
                              type="button"
                              onClick={() => handleToggleChecklistItem(item.id)}
                              aria-pressed={item.status === 'VERIFIED'}
                              className="w-full flex items-start gap-3 rounded-lg px-3 py-2 hover:bg-gray-50 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2"
                            >
                              <span
                                className={`mt-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full border text-[10px] font-semibold ${statusBadgeClasses(
                                  item.status,
                                )}`}
                              >
                                {item.status === 'VERIFIED'
                                  ? '✓'
                                  : item.status === 'IN_REVIEW'
                                  ? '…'
                                  : ''}
                              </span>
                              <div>
                                <p className="text-sm font-medium text-gray-900">
                                  {item.title}
                                </p>
                                {item.description && (
                                  <p className="mt-0.5 text-xs text-gray-500">
                                    {item.description}
                                  </p>
                                )}
                              </div>
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </section>

                {/* Right: Engineer checklist (using section navigator) */}
                <aside className="lg:col-span-1 space-y-4">
                  <div className="bg-white border border-gray-200 rounded-xl shadow-sm">
                    <div className="px-4 py-3 border-b border-gray-200">
                      <h2 className="text-sm font-semibold text-gray-900">
                        Engineer Checklist
                      </h2>
                      <p className="mt-1 text-xs text-gray-500">
                        Work top‑to‑bottom, verifying each section as you go.
                      </p>
                    </div>
                    <div className="divide-y divide-gray-100">
                      {sectionOrder.map((sectionId) => (
                        <button
                          key={sectionId}
                          type="button"
                          onClick={() => setSelectedSection(sectionId)}
                          aria-current={selectedSection === sectionId ? 'page' : undefined}
                          className={`w-full flex items-center justify-between px-4 py-3 text-left text-sm transition-colors ${
                            selectedSection === sectionId
                              ? 'bg-primary-50 text-primary-900'
                              : 'hover:bg-gray-50 text-gray-800'
                          } focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2`}
                        >
                          <div className="flex flex-col">
                            <span className="font-medium">
                              {sectionLabels[sectionId]}
                            </span>
                            <span className="text-[11px] text-gray-500">
                              {
                                checklist.filter(
                                  (item) => item.sectionId === sectionId,
                                ).length
                              }{' '}
                              checks
                            </span>
                          </div>
                          <span
                            className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${statusBadgeClasses(
                              sectionStatus(sectionId),
                            )}`}
                          >
                            {sectionStatus(sectionId) === 'VERIFIED'
                              ? 'Verified'
                              : sectionStatus(sectionId) === 'IN_REVIEW'
                              ? 'In review'
                              : 'Not started'}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
                    <h3 className="text-sm font-semibold text-gray-900">Need a second look?</h3>
                    <p className="mt-2 text-xs text-gray-600">
                      If something doesn’t line up, flag it now so your team can address it before
                      room categorization. You can always return to this step later.
                    </p>
                    <button
                      type="button"
                      onClick={() => alert('In the real product this would open internal notes.')}
                      className="mt-3 inline-flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2"
                    >
                      <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                      </svg>
                      Add internal note
                    </button>
                  </div>

                  <div className="bg-white border border-gray-200 rounded-xl shadow-sm p-4 space-y-3">
                    <h3 className="text-sm font-semibold text-gray-900">
                      What happens next
                    </h3>
                    <p className="text-xs text-gray-600">
                      Once everything here looks good, you&apos;ll move into Room
                      Categorization to organize photos into rooms and categories. This
                      step should feel like a quick “sanity check,” not a full re‑draft.
                    </p>
                    <button
                      type="button"
                      onClick={handleContinue}
                      disabled={!allVerified || isContinuing}
                      className={`w-full inline-flex items-center justify-center rounded-lg px-4 py-2.5 text-sm font-medium shadow-sm transition-colors ${
                        !allVerified || isContinuing
                          ? 'bg-gray-200 text-gray-500 cursor-not-allowed'
                          : 'bg-primary-600 text-white hover:bg-primary-700'
                      }`}
                    >
                      {isContinuing ? 'Continuing…' : 'Continue to Room Categorization'}
                      {!isContinuing && (
                        <svg
                          className="ml-2 h-4 w-4"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M9 5l7 7-7 7"
                          />
                        </svg>
                      )}
                    </button>
                    {!allVerified && (
                      <p className="text-[11px] text-rose-700 bg-rose-50 border border-rose-200 rounded-md px-2 py-1">
                        Verify all checklist items to enable the continue button.
                      </p>
                    )}
                  </div>
                </aside>
              </div>
            </div>
          </div>
        </div>
      </div>
    </ProtectedRoute>
  );
}

function renderSectionContent(
  sectionId: ResourceSectionId,
  resources: AppraisalResources,
) {
  switch (sectionId) {
    case 'subject': {
      const s = resources.subject;
      const l = resources.listing_and_contract;
      return (
        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
              <h3 className="text-sm font-semibold text-gray-900 mb-2">
                Property & Assignment
              </h3>
              <dl className="text-xs text-gray-700 space-y-1">
                <div>
                  <dt className="font-medium text-gray-600">Address</dt>
                  <dd>
                    {s.property_address}, {s.city}, {s.state} {s.zip}
                  </dd>
                </div>
                <div>
                  <dt className="font-medium text-gray-600">Borrower</dt>
                  <dd>{s.borrower}</dd>
                </div>
                <div>
                  <dt className="font-medium text-gray-600">Client / Lender</dt>
                  <dd>{s.lender_client}</dd>
                </div>
                <div>
                  <dt className="font-medium text-gray-600">Assignment Type</dt>
                  <dd>{s.assignment_type}</dd>
                </div>
                <div>
                  <dt className="font-medium text-gray-600">Property Rights</dt>
                  <dd>{s.property_rights_appraised}</dd>
                </div>
              </dl>
            </div>
            <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
              <h3 className="text-sm font-semibold text-gray-900 mb-2">
                Contract Snapshot
              </h3>
              <dl className="text-xs text-gray-700 space-y-1">
                <div>
                  <dt className="font-medium text-gray-600">Contract Price</dt>
                  <dd>${l.contract_price.toLocaleString()}</dd>
                </div>
                <div>
                  <dt className="font-medium text-gray-600">Listing vs. Contract</dt>
                  <dd>
                    List ${l.original_list_price.toLocaleString()} → Contract $
                    {l.contract_price.toLocaleString()}
                  </dd>
                </div>
                <div>
                  <dt className="font-medium text-gray-600">Days on Market</dt>
                  <dd>{l.days_on_market} days</dd>
                </div>
                <div>
                  <dt className="font-medium text-gray-600">Sale Type</dt>
                  <dd>{l.sale_type}</dd>
                </div>
              </dl>
            </div>
          </div>
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
            <h3 className="text-sm font-semibold text-gray-900 mb-2">
              Taxes & Legal
            </h3>
            <dl className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs text-gray-700">
              <div>
                <dt className="font-medium text-gray-600">Tax Year</dt>
                <dd>{s.tax_year}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Annual Taxes</dt>
                <dd>${s.real_estate_taxes.toLocaleString()}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Parcel Numbers</dt>
                <dd>{s.assessors_parcel_numbers.join(', ')}</dd>
              </div>
            </dl>
          </div>
        </div>
      );
    }
    case 'neighborhood': {
      const n = resources.neighborhood;
      return (
        <div className="space-y-4">
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
            <h3 className="text-sm font-semibold text-gray-900 mb-2">
              Market Overview
            </h3>
            <dl className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs text-gray-700">
              <div>
                <dt className="font-medium text-gray-600">Location</dt>
                <dd>{n.location}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Built-Up</dt>
                <dd>{n.built_up}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Growth</dt>
                <dd>{n.growth}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Value Trend</dt>
                <dd>{n.one_unit_value_trend}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Demand / Supply</dt>
                <dd>{n.demand_supply}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Marketing Time</dt>
                <dd>{n.typical_marketing_time}</dd>
              </div>
            </dl>
          </div>
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
            <h3 className="text-sm font-semibold text-gray-900 mb-2">
              Pricing Envelope (1‑Unit)
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs text-gray-700">
              <div>
                <p className="font-medium text-gray-600 mb-1">
                  Active Listings ({n.one_unit_listings.count})
                </p>
                <p>
                  ${n.one_unit_listings.price_range_low.toLocaleString()} – $
                  {n.one_unit_listings.price_range_high.toLocaleString()}
                </p>
              </div>
              <div>
                <p className="font-medium text-gray-600 mb-1">
                  Sales (12 months) ({n.one_unit_sales_12_months.count})
                </p>
                <p>
                  ${n.one_unit_sales_12_months.price_range_low.toLocaleString()} – $
                  {n.one_unit_sales_12_months.price_range_high.toLocaleString()}
                </p>
              </div>
            </div>
          </div>
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-100 space-y-2">
            <h3 className="text-sm font-semibold text-gray-900">Narrative</h3>
            <p className="text-xs text-gray-700">{n.description}</p>
            <p className="text-xs text-gray-700">{n.market_notes}</p>
          </div>
        </div>
      );
    }
    case 'site': {
      const s = resources.site;
      return (
        <div className="space-y-4">
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
            <h3 className="text-sm font-semibold text-gray-900 mb-2">Site</h3>
            <dl className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs text-gray-700">
              <div>
                <dt className="font-medium text-gray-600">Size</dt>
                <dd>{s.area_acres} acres</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Shape</dt>
                <dd>{s.shape}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">View</dt>
                <dd>{s.view}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Zoning</dt>
                <dd>{s.zoning_classification}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Compliance</dt>
                <dd>{s.zoning_compliance}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Highest &amp; Best Use</dt>
                <dd>{s.highest_and_best_use_as_improved}</dd>
              </div>
            </dl>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
              <h3 className="text-sm font-semibold text-gray-900 mb-2">
                Utilities
              </h3>
              <dl className="text-xs text-gray-700 space-y-1">
                <div>
                  <dt className="font-medium text-gray-600">Electric</dt>
                  <dd>{s.utilities.electric}</dd>
                </div>
                <div>
                  <dt className="font-medium text-gray-600">Gas</dt>
                  <dd>{s.utilities.gas}</dd>
                </div>
                <div>
                  <dt className="font-medium text-gray-600">Water</dt>
                  <dd>{s.utilities.water}</dd>
                </div>
                <div>
                  <dt className="font-medium text-gray-600">Sanitary Sewer</dt>
                  <dd>{s.utilities.sanitary_sewer}</dd>
                </div>
              </dl>
            </div>
            <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
              <h3 className="text-sm font-semibold text-gray-900 mb-2">
                Flood & Easements
              </h3>
              <dl className="text-xs text-gray-700 space-y-1">
                <div>
                  <dt className="font-medium text-gray-600">Flood Zone</dt>
                  <dd>
                    {s.flood_zone} (FEMA {s.fema_map_number}, {s.fema_map_date})
                  </dd>
                </div>
                <div>
                  <dt className="font-medium text-gray-600">In Hazard Area?</dt>
                  <dd>{s.flood_hazard_area ? 'Yes' : 'No'}</dd>
                </div>
                <div>
                  <dt className="font-medium text-gray-600">Easements / Encroachments</dt>
                  <dd>{s.easements_encroachments}</dd>
                </div>
              </dl>
            </div>
          </div>
        </div>
      );
    }
    case 'improvements': {
      const g = resources.improvements.general;
      const im =
        resources.improvements.interior_mechanical as InteriorMechanicalSnapshot;
      const heatingSummary = Array.isArray(im.heating?.type)
        ? im.heating?.type?.join(', ')
        : im.heating?.type || '—';
      const fireplaceSummary =
        im.fireplaces?.count !== undefined
          ? `${im.fireplaces.count} ${im.fireplaces.type?.toLowerCase() || ''}`.trim()
          : '—';
      const rooms = im.rooms_above_grade;
      return (
        <div className="space-y-4">
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
            <h3 className="text-sm font-semibold text-gray-900 mb-2">
              Structure Overview
            </h3>
            <dl className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs text-gray-700">
              <div>
                <dt className="font-medium text-gray-600">Design / Style</dt>
                <dd>{g.design_style}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Year Built / Effective</dt>
                <dd>
                  {g.year_built} (effective {g.effective_age_years} yrs)
                </dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Condition / Quality</dt>
                <dd>
                  {g.overall_condition} / {g.overall_quality}
                </dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Stories</dt>
                <dd>{g.stories}‑story, {g.type}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">GLA (Above Grade)</dt>
                <dd>
                  {im.gross_living_area_above_grade_sqft
                    ? `${im.gross_living_area_above_grade_sqft} sf`
                    : '—'}
                </dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Basement</dt>
                <dd>
                  {g.basement_area_sqft} sf, {g.basement_finish_percent}% finished (
                  {g.basement_access})
                </dd>
              </div>
            </dl>
          </div>
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-100 space-y-2">
            <h3 className="text-sm font-semibold text-gray-900">Mechanical & Rooms</h3>
            <p className="text-xs text-gray-700">
              heating: {heatingSummary}; cooling: {im.cooling || '—'}; fireplaces:{' '}
              {fireplaceSummary}
            </p>
            <p className="text-xs text-gray-700">
              Rooms above grade:{' '}
              {rooms
                ? `${rooms.total_rooms} total, ${rooms.bedrooms} beds, ${rooms.bathrooms} baths.`
                : 'Room counts unavailable in this dataset.'}
            </p>
          </div>
        </div>
      );
    }
    case 'sales_comparison': {
      const s = resources.sales_comparison.subject;
      const comps = resources.sales_comparison.comparables;
      return (
        <div className="space-y-4">
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
            <h3 className="text-sm font-semibold text-gray-900 mb-2">
              Subject Positioning
            </h3>
            <dl className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs text-gray-700">
              <div>
                <dt className="font-medium text-gray-600">Contract Price</dt>
                <dd>${s.contract_price.toLocaleString()}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Price / SF</dt>
                <dd>${s.price_per_sqft.toFixed(2)} / sf</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">GLA</dt>
                <dd>{s.gross_living_area_sqft} sf</dd>
              </div>
            </dl>
          </div>
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
            <h3 className="text-sm font-semibold text-gray-900 mb-3">
              Key Comparables
            </h3>
            <div className="space-y-3 text-xs text-gray-700">
              {comps.map((c) => (
                <div
                  key={c.id}
                  className="flex items-start justify-between gap-3 border-b border-gray-100 pb-2 last:border-0 last:pb-0"
                >
                  <div>
                    <p className="font-medium text-gray-900">
                      {c.address}, {c.city}
                    </p>
                    <p className="text-[11px] text-gray-500">
                      {c.proximity} • {c.design} • {c.condition}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="font-semibold text-gray-900">
                      ${c.sale_price.toLocaleString()}
                    </p>
                    <p className="text-[11px] text-gray-500">
                      Adjusted ${c.adjusted_sale_price.toLocaleString()}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      );
    }
    case 'cost_approach': {
      const c = resources.cost_approach;
      return (
        <div className="space-y-4">
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
            <h3 className="text-sm font-semibold text-gray-900 mb-2">
              Cost Summary
            </h3>
            <dl className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs text-gray-700">
              <div>
                <dt className="font-medium text-gray-600">Site Value</dt>
                <dd>${c.site_value.toLocaleString()}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Total Cost New</dt>
                <dd>${c.total_cost_new.toLocaleString()}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Depreciation</dt>
                <dd>${c.depreciation.toLocaleString()}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-600">Indicated Value</dt>
                <dd>
                  ${c.indicated_value_by_cost_approach.toLocaleString()}
                </dd>
              </div>
            </dl>
          </div>
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-100 space-y-2">
            <h3 className="text-sm font-semibold text-gray-900">Comments</h3>
            <p className="text-xs text-gray-700">{c.cost_data_source}</p>
            <p className="text-xs text-gray-700">{c.comments}</p>
          </div>
        </div>
      );
    }
    case 'photos_sketch': {
      const photos = resources.photos;
      const sketch = resources.sketch;
      return (
        <div className="space-y-4">
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
            <h3 className="text-sm font-semibold text-gray-900 mb-2">
              Photo Pages
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs text-gray-700">
              {photos.map((page) => (
                <div
                  key={page.page}
                  className="rounded-lg border border-gray-100 bg-white px-3 py-2"
                >
                  <p className="font-medium text-gray-900 mb-1">
                    Page {page.page}
                  </p>
                  <p className="text-[11px] text-gray-600">
                    {page.labels.join(' • ')}
                  </p>
                </div>
              ))}
            </div>
          </div>
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
            <h3 className="text-sm font-semibold text-gray-900 mb-2">
              Sketch Areas
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs text-gray-700">
              {sketch.areas.map((area, idx) => (
                <div
                  key={`${area.type}-${area.level}-${idx}`}
                  className="rounded-lg border border-gray-100 bg-white px-3 py-2"
                >
                  <p className="font-medium text-gray-900">
                    {area.type.replace(/_/g, ' ')} ({area.level})
                  </p>
                  <p className="text-[11px] text-gray-600">
                    {area.square_feet} sf • {area.notes}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>
      );
    }
    case 'overall': {
      const r = resources.reconciliation;
      return (
        <div className="space-y-4">
          <div className="bg-green-50 rounded-lg p-4 border border-green-100">
            <h3 className="text-sm font-semibold text-gray-900 mb-2">
              Reconciled Value
            </h3>
            <dl className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs text-gray-700">
              <div>
                <dt className="font-medium text-gray-600">Sales Comparison</dt>
                <dd>${r.indicated_value_sales_comparison.toLocaleString()}</dd>
              </div>
              {r.indicated_value_cost_approach && (
                <div>
                  <dt className="font-medium text-gray-600">Cost Approach</dt>
                  <dd>${r.indicated_value_cost_approach.toLocaleString()}</dd>
                </div>
              )}
              <div>
                <dt className="font-medium text-gray-600">Final Value</dt>
                <dd className="font-semibold text-gray-900">
                  ${r.final_market_value.toLocaleString()} ({r.value_condition})
                </dd>
              </div>
            </dl>
          </div>
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-100 space-y-2">
            <h3 className="text-sm font-semibold text-gray-900">Appraiser Notes</h3>
            <p className="text-xs text-gray-700">{r.comments}</p>
          </div>
        </div>
      );
    }
    default:
      return null;
  }
}



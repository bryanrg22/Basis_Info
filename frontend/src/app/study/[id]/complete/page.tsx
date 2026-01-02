'use client';

import { useState, useEffect, useMemo } from 'react';
import { useApp } from '@/contexts/AppContext';
import { formatCurrency, formatDate } from '@/utils/formatting';
import { useParams, useRouter } from 'next/navigation';
import ProtectedRoute from '@/components/ProtectedRoute';
import Header from '@/components/Header';
import Sidebar from '@/components/Sidebar';
import StudyBackButton from '@/components/StudyBackButton';
import { Asset } from '@/types';

export default function CompletionPage() {
  const { state, dispatch, updateWorkflowStatus } = useApp();
  const params = useParams();
  const router = useRouter();
  const studyId = params.id as string;

  const study = state.studies.find(s => s.id === studyId);
  const [isSigned, setIsSigned] = useState(false);
  const [showSignature, setShowSignature] = useState(false);

  useEffect(() => {
    if (!study) {
      router.push('/dashboard');
      return;
    }

    // Navigation guard: allow access based on currentStep and visitedSteps
    const status = study.workflowStatus;
    const currentStep = study.currentStep || status;
    const visitedSteps = study.visitedSteps || [status];
    
    // Allow access if current step is completed, or if it's been visited
    const canAccess = 
      currentStep === 'completed' || 
      visitedSteps.includes('completed') ||
      status === 'completed';

    if (!canAccess) {
      // Redirect to appropriate page based on current step
      const targetStep = currentStep;
      if (targetStep === 'uploading_documents' || targetStep === 'analyzing_rooms') {
        router.push(`/study/${studyId}/analyze/first`);
      } else if (targetStep === 'resource_extraction') {
        router.push(`/study/${studyId}/review/resources`);
      } else if (targetStep === 'reviewing_rooms') {
        router.push(`/study/${studyId}/review/first`);
      } else if (targetStep === 'engineering_takeoff') {
        router.push(`/study/${studyId}/engineering-takeoff`);
      } else {
        router.push('/dashboard');
      }
      return;
    }

    // Update currentStep if needed
    if (currentStep !== 'completed' && status === 'completed') {
      const updatedVisitedSteps = visitedSteps.includes('completed') 
        ? visitedSteps 
        : [...visitedSteps, 'completed'];
      
      updateWorkflowStatus(studyId, 'completed').catch(() => {
        // Silently handle error
      });
    }
  }, [study, router, studyId, updateWorkflowStatus]);

  // Get verified assets from study (all assets at this stage are considered verified)
  const verifiedAssets = useMemo(() => {
    return study?.assets || [];
  }, [study?.assets]);

  const totalVerifiedValue = useMemo(() =>
    verifiedAssets.reduce((sum, asset) => sum + asset.estimatedValue, 0),
    [verifiedAssets]
  );

  // Calculate report summary from real assets
  const reportSummary = useMemo(() => {
    const assets = verifiedAssets;
    const byCategory: Record<string, { count: number; value: number }> = {
      '5-year': { count: 0, value: 0 },
      '15-year': { count: 0, value: 0 },
      '27.5-year': { count: 0, value: 0 },
    };

    assets.forEach(asset => {
      const category = asset.category;
      if (byCategory[category]) {
        byCategory[category].count += 1;
        byCategory[category].value += asset.estimatedValue;
      }
    });

    // Accelerated depreciation = 5-year + 15-year assets
    const acceleratedDepreciation =
      byCategory['5-year'].value + byCategory['15-year'].value;
    const acceleratedPercentage = totalVerifiedValue > 0
      ? Math.round((acceleratedDepreciation / totalVerifiedValue) * 100)
      : 0;

    return {
      byCategory,
      acceleratedDepreciation,
      acceleratedPercentage,
    };
  }, [verifiedAssets, totalVerifiedValue]);

  if (!study) {
    return <div>Loading...</div>;
  }

  const handleSignOff = async () => {
    setShowSignature(true);
    setTimeout(async () => {
      setIsSigned(true);
      setShowSignature(false);
      
      // Update study status to completed
      if (study) {
        const updatedStudy = { ...study, status: 'completed' as const };
        dispatch({ type: 'UPDATE_STUDY', payload: updatedStudy });
        // Update workflow status and persist to Firestore
        try {
          await updateWorkflowStatus(study.id, 'completed');
        } catch (error) {
          console.error('Error updating workflow status:', error);
        }
      }
    }, 2000);
  };

  const handleExportReport = () => {
    // TODO: Implement real PDF export
    const fileName = `${study.propertyName.replace(/\s+/g, '_')}_Cost_Segregation_Report.pdf`;
    alert(`Report exported as: ${fileName}`);
  };

  return (
    <ProtectedRoute>
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Header />
          <div className="flex-1 overflow-y-auto">
            <div className="p-6 max-w-4xl mx-auto">
      {/* Back Button */}
      <div className="mb-4">
        <StudyBackButton />
      </div>
      {/* Header */}
      <div className="mb-8 text-center">
        <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
          <svg className="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <h1 className="text-3xl font-bold text-gray-900">Analysis Complete!</h1>
        <p className="text-gray-600 mt-2">Your cost segregation study has been successfully verified</p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <div className="bg-white p-4 rounded-lg shadow-sm border border-gray-200">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Assets Verified</p>
          <p className="text-xl font-bold text-gray-900 mt-1">{verifiedAssets.length}</p>
        </div>

        <div className="bg-white p-4 rounded-lg shadow-sm border border-gray-200">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Total Value</p>
          <p className="text-xl font-bold text-gray-900 mt-1">{formatCurrency(totalVerifiedValue)}</p>
        </div>

        <div className="bg-white p-4 rounded-lg shadow-sm border border-gray-200">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Accelerated Depreciation</p>
          <p className="text-xl font-bold text-gray-900 mt-1">{formatCurrency(reportSummary.acceleratedDepreciation)}</p>
          <p className="text-[10px] text-gray-500 mt-0.5">{reportSummary.acceleratedPercentage}% of total</p>
        </div>

        <div className="bg-white p-4 rounded-lg shadow-sm border border-gray-200">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Completed</p>
          <p className="text-xl font-bold text-gray-900 mt-1">{formatDate(study.analysisDate)}</p>
        </div>
      </div>

      {/* Depreciation Category Breakdown */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-8">
        <div className="bg-white p-3 rounded-lg border border-gray-200">
          <div className="flex items-center justify-between">
            <div className="min-w-0 flex-1">
              <p className="text-[10px] font-medium text-gray-500 uppercase tracking-wide">5-Year Property</p>
              <p className="text-base font-bold text-gray-900 mt-0.5 truncate">
                {formatCurrency(reportSummary.byCategory['5-year']?.value || 0)}
              </p>
            </div>
            <div className="text-right ml-3 flex-shrink-0">
              <p className="text-lg font-bold text-gray-900">
                {reportSummary.byCategory['5-year']?.count || 0}
              </p>
              <p className="text-[10px] text-gray-500">assets</p>
            </div>
          </div>
        </div>

        <div className="bg-white p-3 rounded-lg border border-gray-200">
          <div className="flex items-center justify-between">
            <div className="min-w-0 flex-1">
              <p className="text-[10px] font-medium text-gray-500 uppercase tracking-wide">15-Year Property</p>
              <p className="text-base font-bold text-gray-900 mt-0.5 truncate">
                {formatCurrency(reportSummary.byCategory['15-year']?.value || 0)}
              </p>
            </div>
            <div className="text-right ml-3 flex-shrink-0">
              <p className="text-lg font-bold text-gray-900">
                {reportSummary.byCategory['15-year']?.count || 0}
              </p>
              <p className="text-[10px] text-gray-500">assets</p>
            </div>
          </div>
        </div>

        <div className="bg-white p-3 rounded-lg border border-gray-200">
          <div className="flex items-center justify-between">
            <div className="min-w-0 flex-1">
              <p className="text-[10px] font-medium text-gray-500 uppercase tracking-wide">27.5-Year Property</p>
              <p className="text-base font-bold text-gray-900 mt-0.5 truncate">
                {formatCurrency(reportSummary.byCategory['27.5-year']?.value || 0)}
              </p>
            </div>
            <div className="text-right ml-3 flex-shrink-0">
              <p className="text-lg font-bold text-gray-900">
                {reportSummary.byCategory['27.5-year']?.count || 0}
              </p>
              <p className="text-[10px] text-gray-500">assets</p>
            </div>
          </div>
        </div>
      </div>

      {/* Final Report Summary */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 mb-8">
        <div className="p-6 border-b border-gray-200">
          <h2 className="text-xl font-semibold text-gray-900">Final Report Summary</h2>
          <p className="text-gray-600 mt-1">Property: {study.propertyName}</p>
        </div>

        <div className="p-6">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Depreciation Period
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Asset Description
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Value
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Status
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {verifiedAssets.map((asset) => (
                  <tr key={asset.id}>
                    <td className="px-4 py-4 whitespace-nowrap">
                      <span className={`inline-flex px-3 py-1 text-sm font-semibold rounded-full ${
                        asset.category === '5-year' 
                          ? 'bg-blue-100 text-blue-800'
                          : asset.category === '15-year'
                          ? 'bg-green-100 text-green-800'
                          : 'bg-yellow-100 text-yellow-800'
                      }`}>
                        {asset.depreciationPeriod} years
                      </span>
                    </td>
                    <td className="px-4 py-4 whitespace-nowrap">
                      <div>
                        <div className="text-sm font-medium text-gray-900">{asset.name}</div>
                        <div className="text-sm text-gray-500">{asset.description}</div>
                      </div>
                    </td>
                    <td className="px-4 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                      {formatCurrency(asset.estimatedValue)}
                    </td>
                    <td className="px-4 py-4 whitespace-nowrap">
                      <span className="inline-flex px-2 py-1 text-xs font-semibold rounded-full bg-green-100 text-green-800">
                        Verified ✓
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Signature Section */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 mb-8">
        <div className="p-6">
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Engineer Verification</h2>
          
          {!isSigned ? (
            <div className="text-center py-8">
              <p className="text-gray-600 mb-6">Please sign off on this study to finalize the report</p>
              <button
                onClick={handleSignOff}
                className="bg-primary-600 text-white px-8 py-3 rounded-lg font-medium hover:bg-primary-700 transition-colors"
              >
                Sign Off & Verify
              </button>
            </div>
          ) : (
            <div className="text-center py-8">
              <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
                <svg className="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <p className="text-green-600 font-medium mb-2">Study Verified & Signed</p>
              <p className="text-sm text-gray-500">Engineer: {state.user.name}</p>
              <p className="text-sm text-gray-500">Date: {new Date().toLocaleDateString()}</p>
            </div>
          )}

          {showSignature && (
            <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
              <div className="bg-white p-8 rounded-lg text-center">
                <div className="animate-spin w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full mx-auto mb-4"></div>
                <p className="text-gray-600">Applying digital signature...</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Export Section */}
      <div className="bg-gradient-to-r from-primary-500 to-primary-600 rounded-lg p-6 text-white">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold mb-2">Ready to Export</h2>
            <p className="text-primary-100">Download your finalized cost segregation report</p>
          </div>
          <div className="flex space-x-3">
            <button
              onClick={handleExportReport}
              disabled={!isSigned}
              className="bg-white text-primary-600 px-6 py-3 rounded-lg font-medium hover:bg-gray-50 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
            >
              Export PDF
            </button>
            <button
              onClick={() => router.push('/dashboard')}
              className="border border-white text-white px-6 py-3 rounded-lg font-medium hover:bg-white hover:text-primary-600 transition-colors"
            >
              Return to Dashboard
            </button>
          </div>
        </div>
      </div>
            </div>
          </div>
        </div>
      </div>
    </ProtectedRoute>
  );
}

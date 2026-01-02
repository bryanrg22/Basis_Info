'use client';

import { useApp } from '@/contexts/AppContext';
import { formatCurrency, formatDate } from '@/utils/formatting';
import { getWorkflowStatusLabel, getWorkflowStatusColor, getWorkflowPageUrl } from '@/utils/workflow';
import Link from 'next/link';
import PageLayout from '@/components/layout/PageLayout';

export default function Dashboard() {
  const { state, calculateStatistics } = useApp();
  const statistics = calculateStatistics();
  const { studies } = state;
  const hasStudies = studies.length > 0;

  return (
    <PageLayout>
      <div className="px-6 py-8 md:px-10 lg:px-16">
        {/* Header */}
        <div className="mb-8 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-primary-600">
              Overview
            </p>
            <h1 className="mt-1 text-3xl font-bold text-gray-900">Dashboard</h1>
            <p className="text-gray-600 mt-1">
              Welcome back, {state.user.name}. Here&apos;s a snapshot of your studies and
              projected tax savings.
            </p>
          </div>
          {hasStudies && (
            <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm text-gray-700 shadow-sm">
              <p className="font-medium">You have {studies.length} study{studies.length > 1 ? 'ies' : ''} in this workspace.</p>
              <p className="text-xs text-gray-500 mt-1">
                Continue an in‑progress study or start a new one with the actions below.
              </p>
            </div>
          )}
        </div>

        {/* Statistics Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
          <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
            <div>
              <p className="text-sm font-medium text-gray-600">Studies Completed</p>
              <p className="text-2xl font-bold text-gray-900">{statistics.studiesCompleted}</p>
              <p className="mt-1 text-xs text-gray-500">All‑time across this workspace</p>
            </div>
          </div>

          <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
            <div>
              <p className="text-sm font-medium text-gray-600">Revenue Generated</p>
              <p className="text-2xl font-bold text-gray-900">{formatCurrency(statistics.revenueGenerated)}</p>
              <p className="mt-1 text-xs text-gray-500">Estimated fee revenue from completed studies</p>
            </div>
          </div>

          <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
            <div>
              <p className="text-sm font-medium text-gray-600">Tax Savings Provided</p>
              <p className="text-2xl font-bold text-gray-900">{formatCurrency(statistics.taxSavingsProvided)}</p>
              <p className="mt-1 text-xs text-gray-500">Modeled savings delivered to your clients</p>
            </div>
          </div>
        </div>

        {/* Quick insights */}
        {hasStudies && (
          <div className="mb-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm shadow-sm">
              <p className="text-xs font-medium text-gray-500">Most recent study</p>
              <p className="mt-1 font-semibold text-gray-900">
                {studies[0].propertyName}
              </p>
              <p className="mt-1 text-xs text-gray-500">
                Status:{' '}
                <span
                  className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${getWorkflowStatusColor(
                    studies[0].workflowStatus,
                  )}`}
                >
                  {getWorkflowStatusLabel(studies[0].workflowStatus)}
                </span>
              </p>
            </div>
            <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm shadow-sm">
              <p className="text-xs font-medium text-gray-500">Total assets analyzed</p>
              <p className="mt-1 font-semibold text-gray-900">
                {formatCurrency(statistics.totalAssets || studies[0].totalAssets || 0)}
              </p>
              <p className="mt-1 text-xs text-gray-500">
                Sum of all assets across completed studies.
              </p>
            </div>
            <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm shadow-sm">
              <p className="text-xs font-medium text-gray-500">Workflow snapshot</p>
              <p className="mt-1 font-semibold text-gray-900">
                {statistics.inProgressStudies || 0} in progress · {statistics.studiesCompleted} completed
              </p>
              <p className="mt-1 text-xs text-gray-500">
                Use this to balance engineering and review capacity.
              </p>
            </div>
          </div>
        )}

        {/* Create New Study CTA */}
        <section className="mb-8">
          <div className="bg-gradient-to-r from-primary-500 to-primary-600 rounded-2xl px-6 py-5 text-white shadow-sm">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-lg font-semibold">Ready to start a new study?</h2>
                <p className="text-primary-100 text-sm mt-1">
                  Upload an appraisal and property photos and let Basis pre‑classify rooms, assets, and takeoffs.
                </p>
              </div>
              <div className="flex flex-col gap-2 sm:items-end">
                <Link
                  href="/study/new"
                  className="inline-flex items-center justify-center rounded-lg bg-white px-6 py-2.5 text-sm font-medium text-primary-600 shadow-sm transition-colors hover:bg-gray-50"
                >
                  Create New Study
                  <svg
                    className="ml-2 h-4 w-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                </Link>
              </div>
            </div>
          </div>
        </section>

        {/* Previous Studies */}
        <section className="bg-white rounded-2xl shadow-sm border border-gray-200">
          <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Previous Studies</h2>
              <p className="mt-1 text-xs text-gray-500">
                Jump back into an in‑progress workflow or review a completed report.
              </p>
            </div>
          </div>
          {hasStudies ? (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200 text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">
                      Property
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">
                      Status
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">
                      Analysis Date
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">
                      Total Assets
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wide">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {studies.map((study) => (
                    <tr key={study.id} className="hover:bg-gray-50 transition-colors">
                      <td className="px-6 py-4">
                        <div className="flex flex-col">
                          <span className="font-medium text-gray-900">
                            {study.propertyName}
                          </span>
                          <span className="text-xs text-gray-500">
                            Study ID: {study.id}
                          </span>
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <span
                          className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${getWorkflowStatusColor(
                            study.workflowStatus,
                          )}`}
                        >
                          {getWorkflowStatusLabel(study.workflowStatus)}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-gray-700">
                        {formatDate(study.analysisDate)}
                      </td>
                      <td className="px-6 py-4 text-gray-700">
                        {formatCurrency(study.totalAssets)}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <div className="flex items-center justify-end gap-2">
                          <Link
                            href={getWorkflowPageUrl(study.id, study.workflowStatus)}
                            className="inline-flex items-center rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
                          >
                            {study.workflowStatus === 'completed' ? 'View report' : 'Continue'}
                          </Link>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="px-6 py-10 flex flex-col items-center text-center">
              <p className="text-sm font-medium text-gray-900">No studies yet</p>
              <p className="mt-1 text-xs text-gray-500 max-w-sm">
                When you create your first study, it will appear here with quick links to
                resume the workflow or view the completed report.
              </p>
              <Link
                href="/study/new"
                className="mt-4 inline-flex items-center rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-700"
              >
                Create your first study
              </Link>
            </div>
          )}
        </section>
      </div>
    </PageLayout>
  );
}

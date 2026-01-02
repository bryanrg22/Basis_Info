'use client';

/**
 * Classification Tab
 * 
 * Wizard-style classification workflow with IRS rule suggestions.
 */

import { useState, useMemo } from 'react';
import { AssetDemo, AssetTakeoffDemo, ClassificationInfoDemo } from '@/types/asset-takeoff.types';
import { WIZARD_QUESTIONS } from '@/config/classification-wizard';

interface ClassificationTabProps {
  asset: AssetDemo;
  takeoff: AssetTakeoffDemo;
  onUpdateTakeoff: (updates: Partial<AssetTakeoffDemo>) => void;
}

const CLASSIFICATION_OPTIONS = [
  { code: '00.11', description: 'Office Furniture, Fixtures, and Equipment', years: 7 },
  { code: '57.0', description: 'Distributive Trades and Services', years: 15 },
  { code: '57.1', description: 'Land Improvements', years: 15 },
  { code: '1250', description: 'Real Property - Building Structure', years: 39 },
];

export function ClassificationTab({ asset, takeoff, onUpdateTakeoff }: ClassificationTabProps) {
  const [wizardAnswers, setWizardAnswers] = useState<Record<string, string | boolean | null>>(
    takeoff.classification.wizardAnswers
  );
  const [isOverriding, setIsOverriding] = useState(false);
  const [overrideCode, setOverrideCode] = useState(takeoff.classification.appliedCode ?? '');
  
  const handleAnswerChange = (questionId: string, value: string | boolean | null) => {
    const newAnswers = { ...wizardAnswers, [questionId]: value };
    setWizardAnswers(newAnswers);
    
    // Update takeoff with new answers
    const newClassification: ClassificationInfoDemo = {
      ...takeoff.classification,
      wizardAnswers: newAnswers,
    };
    onUpdateTakeoff({ classification: newClassification });
  };
  
  const handleApplySuggestion = () => {
    const newClassification: ClassificationInfoDemo = {
      ...takeoff.classification,
      appliedCode: takeoff.classification.suggestedCode,
      appliedDescription: takeoff.classification.suggestedDescription,
      decisionSource: 'SUGGESTED',
    };
    onUpdateTakeoff({ classification: newClassification });
  };
  
  const handleApplyOverride = () => {
    const selectedOption = CLASSIFICATION_OPTIONS.find(o => o.code === overrideCode);
    if (!selectedOption) return;
    
    const newClassification: ClassificationInfoDemo = {
      ...takeoff.classification,
      appliedCode: selectedOption.code,
      appliedDescription: selectedOption.description,
      decisionSource: 'OVERRIDDEN',
    };
    onUpdateTakeoff({ classification: newClassification });
    setIsOverriding(false);
  };
  
  const handleClearClassification = () => {
    const newClassification: ClassificationInfoDemo = {
      ...takeoff.classification,
      appliedCode: undefined,
      appliedDescription: undefined,
      decisionSource: undefined,
    };
    onUpdateTakeoff({ classification: newClassification });
  };
  
  // Calculate completion percentage
  const answeredQuestions = Object.values(wizardAnswers).filter(v => v !== null && v !== undefined).length;
  const completionPercent = Math.round((answeredQuestions / WIZARD_QUESTIONS.length) * 100);
  
  const isClassified = !!takeoff.classification.appliedCode;
  
  return (
    <div className="p-6">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: Wizard Questions */}
        <div className="space-y-6">
          {/* Progress */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-lg font-semibold text-gray-900">Classification Wizard</h3>
              <span className="text-sm text-gray-500">{completionPercent}% complete</span>
            </div>
            <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
              <div
                className="h-full bg-primary-600 rounded-full transition-all duration-300"
                style={{ width: `${completionPercent}%` }}
              />
            </div>
          </div>
          
          {/* Questions */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm divide-y divide-gray-100">
            {WIZARD_QUESTIONS.map((question, index) => (
              <div key={question.id} className="p-4">
                <div className="flex items-start gap-3">
                  <span className="flex-shrink-0 w-6 h-6 rounded-full bg-gray-100 text-gray-600 text-sm font-medium flex items-center justify-center">
                    {index + 1}
                  </span>
                  <div className="flex-1">
                    <p className="text-sm font-medium text-gray-900 mb-3">{question.question}</p>
                    
                    {question.type === 'boolean' ? (
                      <div className="flex gap-2">
                        <button
                          onClick={() => handleAnswerChange(question.id, true)}
                          className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                            wizardAnswers[question.id] === true
                              ? 'bg-primary-600 text-white'
                              : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                          }`}
                        >
                          Yes
                        </button>
                        <button
                          onClick={() => handleAnswerChange(question.id, false)}
                          className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                            wizardAnswers[question.id] === false
                              ? 'bg-primary-600 text-white'
                              : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                          }`}
                        >
                          No
                        </button>
                        {wizardAnswers[question.id] !== null && wizardAnswers[question.id] !== undefined && (
                          <button
                            onClick={() => handleAnswerChange(question.id, null)}
                            className="px-3 py-2 text-sm text-gray-400 hover:text-gray-600"
                          >
                            Clear
                          </button>
                        )}
                      </div>
                    ) : (
                      <select
                        value={(wizardAnswers[question.id] as string) ?? ''}
                        onChange={(e) => handleAnswerChange(question.id, e.target.value || null)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                      >
                        <option value="">Select an option...</option>
                        {question.options?.map((opt) => (
                          <option key={opt.value} value={opt.value}>
                            {opt.label}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
        
        {/* Right: Suggestion & IRS Rules */}
        <div className="space-y-6">
          {/* Current Classification Status */}
          {isClassified && (
            <div className={`rounded-xl border-2 p-6 ${
              takeoff.classification.decisionSource === 'OVERRIDDEN'
                ? 'bg-amber-50 border-amber-300'
                : 'bg-primary-50 border-primary-300'
            }`}>
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                      takeoff.classification.decisionSource === 'OVERRIDDEN'
                        ? 'bg-amber-200 text-amber-800'
                        : 'bg-primary-200 text-primary-800'
                    }`}>
                      {takeoff.classification.decisionSource === 'OVERRIDDEN' ? 'Manual Override' : 'Applied'}
                    </span>
                  </div>
                  <p className="text-2xl font-bold text-gray-900">{takeoff.classification.appliedCode}</p>
                  <p className="text-gray-600 mt-1">{takeoff.classification.appliedDescription}</p>
                </div>
                <button
                  onClick={handleClearClassification}
                  className="text-sm text-gray-500 hover:text-gray-700"
                >
                  Clear
                </button>
              </div>
            </div>
          )}
          
          {/* AI Suggestion */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <div className="flex items-center gap-2 mb-4">
              <div className="w-8 h-8 rounded-full bg-primary-100 flex items-center justify-center">
                <svg className="h-4 w-4 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                </svg>
              </div>
              <h3 className="text-lg font-semibold text-gray-900">AI Suggestion</h3>
            </div>
            
            <div className="bg-gray-50 rounded-lg p-4 mb-4">
              <p className="text-xl font-bold text-gray-900">{takeoff.classification.suggestedCode}</p>
              <p className="text-gray-600">{takeoff.classification.suggestedDescription}</p>
              
              {/* Confidence */}
              <div className="mt-3">
                <div className="flex items-center justify-between text-sm mb-1">
                  <span className="text-gray-500">Confidence</span>
                  <span className="font-medium text-gray-900">{Math.round(takeoff.classification.confidence * 100)}%</span>
                </div>
                <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${
                      takeoff.classification.confidence >= 0.9 ? 'bg-green-500' :
                      takeoff.classification.confidence >= 0.7 ? 'bg-amber-500' : 'bg-red-500'
                    }`}
                    style={{ width: `${takeoff.classification.confidence * 100}%` }}
                  />
                </div>
              </div>
            </div>
            
            <div className="flex gap-2">
              {!isClassified && (
                <button
                  onClick={handleApplySuggestion}
                  className="flex-1 px-4 py-2 bg-primary-600 text-white font-medium rounded-lg hover:bg-primary-700 transition-colors"
                >
                  Apply Suggestion
                </button>
              )}
              <button
                onClick={() => setIsOverriding(!isOverriding)}
                className={`flex-1 px-4 py-2 font-medium rounded-lg transition-colors ${
                  isOverriding
                    ? 'bg-gray-800 text-white'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                }`}
              >
                {isOverriding ? 'Cancel' : 'Manual Override'}
              </button>
            </div>
          </div>
          
          {/* Manual Override Form */}
          {isOverriding && (
            <div className="bg-amber-50 rounded-xl border border-amber-200 p-6">
              <h4 className="font-semibold text-gray-900 mb-4">Manual Classification</h4>
              
              <div className="space-y-4">
                <div>
                  <label className="block text-sm text-gray-600 mb-1">Select Classification</label>
                  <select
                    value={overrideCode}
                    onChange={(e) => setOverrideCode(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                  >
                    <option value="">Select a classification...</option>
                    {CLASSIFICATION_OPTIONS.map((opt) => (
                      <option key={opt.code} value={opt.code}>
                        {opt.code} - {opt.description} ({opt.years}-year)
                      </option>
                    ))}
                  </select>
                </div>
                
                <button
                  onClick={handleApplyOverride}
                  disabled={!overrideCode}
                  className="w-full px-4 py-2 bg-amber-600 text-white font-medium rounded-lg hover:bg-amber-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Apply Override
                </button>
              </div>
            </div>
          )}
          
          {/* IRS Rules */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">IRS Rule References</h3>
            
            <div className="space-y-3">
              {takeoff.classification.irsRuleRefs.map((rule) => (
                <div
                  key={rule.id}
                  className="p-4 rounded-lg bg-gray-50 border border-gray-200"
                >
                  <p className="text-xs font-semibold text-primary-700 mb-1">{rule.codeSection}</p>
                  <p className="text-sm font-medium text-gray-900">{rule.title}</p>
                  <p className="text-xs text-gray-600 mt-2 line-clamp-3">{rule.shortExcerpt}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}


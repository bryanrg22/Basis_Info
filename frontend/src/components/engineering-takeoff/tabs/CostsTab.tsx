'use client';

/**
 * Costs Tab
 * 
 * Tab for viewing and editing cost breakdown with historical comparisons.
 */

import { useState, useMemo } from 'react';
import { AssetDemo, AssetTakeoffDemo, CostInfoDemo, CostBreakdownLineDemo } from '@/types/asset-takeoff.types';

interface CostsTabProps {
  asset: AssetDemo;
  takeoff: AssetTakeoffDemo;
  onUpdateTakeoff: (updates: Partial<AssetTakeoffDemo>) => void;
}

export function CostsTab({ asset, takeoff, onUpdateTakeoff }: CostsTabProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState<string>('');
  
  const totalActual = useMemo(() => 
    takeoff.costs.breakdown.reduce((sum, item) => sum + item.amount, 0),
    [takeoff.costs.breakdown]
  );
  
  const variance = totalActual - takeoff.costs.estimatedTotal;
  const variancePercent = Math.round((variance / takeoff.costs.estimatedTotal) * 100);
  
  const handleStartEdit = (item: CostBreakdownLineDemo) => {
    if (!item.editable) return;
    setEditingId(item.id);
    setEditValue(item.amount.toString());
  };
  
  const handleSaveEdit = () => {
    if (!editingId) return;
    
    const newAmount = parseFloat(editValue);
    if (isNaN(newAmount)) {
      setEditingId(null);
      return;
    }
    
    const newBreakdown = takeoff.costs.breakdown.map(item =>
      item.id === editingId ? { ...item, amount: newAmount } : item
    );
    
    const newActualTotal = newBreakdown.reduce((sum, item) => sum + item.amount, 0);
    
    const newCosts: CostInfoDemo = {
      ...takeoff.costs,
      breakdown: newBreakdown,
      actualTotal: newActualTotal,
    };
    
    onUpdateTakeoff({ costs: newCosts });
    setEditingId(null);
  };
  
  const handleCancelEdit = () => {
    setEditingId(null);
    setEditValue('');
  };
  
  return (
    <div className="p-6">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Cost Breakdown */}
        <div className="lg:col-span-2 space-y-6">
          {/* Summary Cards */}
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
              <p className="text-sm text-gray-500 mb-1">Actual Total</p>
              <p className="text-2xl font-bold text-gray-900">
                ${totalActual.toLocaleString()}
              </p>
            </div>
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
              <p className="text-sm text-gray-500 mb-1">Estimated Total</p>
              <p className="text-2xl font-bold text-gray-900">
                ${takeoff.costs.estimatedTotal.toLocaleString()}
              </p>
            </div>
            <div className={`rounded-xl border shadow-sm p-4 ${
              variance > 0 
                ? 'bg-red-50 border-red-200' 
                : variance < 0 
                  ? 'bg-green-50 border-green-200'
                  : 'bg-gray-50 border-gray-200'
            }`}>
              <p className="text-sm text-gray-500 mb-1">Variance</p>
              <p className={`text-2xl font-bold ${
                variance > 0 ? 'text-red-600' : variance < 0 ? 'text-green-600' : 'text-gray-900'
              }`}>
                {variance > 0 ? '+' : ''}{variancePercent}%
              </p>
            </div>
          </div>
          
          {/* Cost Breakdown Table */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-100">
              <h3 className="text-lg font-semibold text-gray-900">Cost Breakdown</h3>
            </div>
            
            <div className="divide-y divide-gray-100">
              {takeoff.costs.breakdown.map((item) => (
                <div
                  key={item.id}
                  className={`px-6 py-4 flex items-center justify-between ${
                    item.editable ? 'hover:bg-gray-50' : ''
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-gray-100 flex items-center justify-center">
                      <svg className="h-4 w-4 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                      </svg>
                    </div>
                    <span className="font-medium text-gray-900">{item.label}</span>
                  </div>
                  
                  {editingId === item.id ? (
                    <div className="flex items-center gap-2">
                      <span className="text-gray-500">$</span>
                      <input
                        type="number"
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        className="w-24 px-2 py-1 border border-gray-300 rounded text-right focus:outline-none focus:ring-2 focus:ring-primary-500"
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleSaveEdit();
                          if (e.key === 'Escape') handleCancelEdit();
                        }}
                      />
                      <button
                        onClick={handleSaveEdit}
                        className="p-1 text-primary-600 hover:text-primary-700"
                      >
                        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                        </svg>
                      </button>
                      <button
                        onClick={handleCancelEdit}
                        className="p-1 text-gray-400 hover:text-gray-600"
                      >
                        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <span className="text-lg font-semibold text-gray-900">
                        ${item.amount.toLocaleString()}
                      </span>
                      {item.editable && (
                        <button
                          onClick={() => handleStartEdit(item)}
                          className="p-1 text-gray-400 hover:text-gray-600 opacity-0 group-hover:opacity-100 transition-opacity"
                          style={{ opacity: 1 }} // Always visible for demo
                        >
                          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                          </svg>
                        </button>
                      )}
                    </div>
                  )}
                </div>
              ))}
              
              {/* Total Row */}
              <div className="px-6 py-4 bg-gray-50 flex items-center justify-between">
                <span className="font-semibold text-gray-900">Total</span>
                <span className="text-xl font-bold text-gray-900">
                  ${totalActual.toLocaleString()}
                </span>
              </div>
            </div>
          </div>
        </div>
        
        {/* Right: Context */}
        <div className="space-y-6">
          {/* Cost Sources */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Cost Sources</h3>
            
            <div className="space-y-3">
              <div className="flex items-center gap-3 p-3 rounded-lg bg-gray-50">
                <div className="w-8 h-8 rounded-lg bg-blue-100 flex items-center justify-center">
                  <svg className="h-4 w-4 text-blue-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                </div>
                <div>
                  <p className="font-medium text-gray-900 text-sm">Schedule of Values</p>
                  <p className="text-xs text-gray-500">Primary source</p>
                </div>
              </div>
              
              <div className="flex items-center gap-3 p-3 rounded-lg bg-gray-50">
                <div className="w-8 h-8 rounded-lg bg-primary-100 flex items-center justify-center">
                  <svg className="h-4 w-4 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </div>
                <div>
                  <p className="font-medium text-gray-900 text-sm">RSMeans Database</p>
                  <p className="text-xs text-gray-500">Estimation fallback</p>
                </div>
              </div>
              
              <div className="flex items-center gap-3 p-3 rounded-lg bg-gray-50">
                <div className="w-8 h-8 rounded-lg bg-purple-100 flex items-center justify-center">
                  <svg className="h-4 w-4 text-purple-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
                  </svg>
                </div>
                <div>
                  <p className="font-medium text-gray-900 text-sm">Internal Cost DB</p>
                  <p className="text-xs text-gray-500">Historical data</p>
                </div>
              </div>
            </div>
          </div>
          
          {/* Historical Notes */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Historical Context</h3>
            
            <div className="p-4 rounded-lg bg-amber-50 border border-amber-200">
              <div className="flex items-start gap-2">
                <svg className="h-5 w-5 text-amber-600 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <p className="text-sm text-amber-800">
                  {takeoff.costs.historicalNotes}
                </p>
              </div>
            </div>
          </div>
          
          {/* Quick Actions */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Quick Actions</h3>
            
            <div className="space-y-2">
              <button
                onClick={() => alert('Would open RSMeans lookup dialog')}
                className="w-full px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors text-left flex items-center gap-2"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                Look up in RSMeans
              </button>
              
              <button
                onClick={() => alert('Would open invoice attachment dialog')}
                className="w-full px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors text-left flex items-center gap-2"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
                </svg>
                Attach Invoice
              </button>
              
              <button
                onClick={() => alert('Would export cost breakdown to Excel')}
                className="w-full px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors text-left flex items-center gap-2"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                Export to Excel
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}


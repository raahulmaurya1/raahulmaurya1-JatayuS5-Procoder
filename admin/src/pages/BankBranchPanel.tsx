import { useState, useEffect } from 'react';
import { ChevronLeft, Edit2, Trash2, Plus } from 'lucide-react';
import { Link } from 'react-router-dom';

interface Branch {
  ifsc: string;
  branch_name: string;
  branch_address: string;
  supported_account_type: string;
  manager_name?: string;
  manager_email?: string;
  manager_phone?: string;
  relationship_officer?: string;
}

export function BankBranchPanel() {
  const [branches, setBranches] = useState<Branch[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [modalMode, setModalMode] = useState<'create' | 'edit'>('create');
  const [formData, setFormData] = useState<Partial<Branch>>({});
  const [loading, setLoading] = useState(true);

  const fetchBranches = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL || ''}/api/v1/bank-branches`);
      if (res.ok) {
        const data = await res.json();
        setBranches(data);
      }
    } catch (err) {
      console.error('Failed to fetch branches', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchBranches();
  }, []);

  const openModal = (mode: 'create' | 'edit', ifsc?: string) => {
    setModalMode(mode);
    if (mode === 'edit' && ifsc) {
      const branch = branches.find(b => b.ifsc === ifsc);
      if (branch) setFormData({ ...branch });
    } else {
      setFormData({});
    }
    setModalOpen(true);
  };

  const handleFormSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const url = modalMode === 'create' 
        ? `${import.meta.env.VITE_API_BASE_URL || ''}/api/v1/admin/bank-branches`
        : `${import.meta.env.VITE_API_BASE_URL || ''}/api/v1/admin/bank-branches/${formData.ifsc}`;
        
      const method = modalMode === 'create' ? 'POST' : 'PUT';
      
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData)
      });

      if (!res.ok) {
        const err = await res.json();
        alert('Error: ' + err.detail);
        return;
      }

      setModalOpen(false);
      fetchBranches();
    } catch (err) {
      alert('Failed to save branch');
    }
  };

  const deleteBranch = async (ifsc: string) => {
    if (!confirm(`Are you sure you want to delete branch ${ifsc}?`)) return;
    
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL || ''}/api/v1/admin/bank-branches/${ifsc}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Failed to delete');
      fetchBranches();
    } catch (err) {
      alert('Failed to delete branch');
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col pt-8 px-4 sm:px-6 lg:px-8">
      <div className="max-w-6xl mx-auto w-full">
        <div className="flex items-center mb-2">
            <Link to="/" className="text-sm text-gray-500 hover:text-indigo-600 flex items-center transition-colors">
              <ChevronLeft className="w-4 h-4 mr-1" /> Dashboard
            </Link>
        </div>
        
        <div className="flex justify-between items-center mb-8">
          <h1 className="text-3xl font-extrabold tracking-tight text-gray-900">Bank Branches</h1>
          <button 
            onClick={() => openModal('create')}
            className="flex items-center text-sm px-4 py-2 bg-emerald-600 text-white rounded-lg font-semibold hover:bg-emerald-700 transition-colors shadow-sm"
          >
            <Plus className="w-4 h-4 mr-1" /> Add Branch
          </button>
        </div>

        <div className="bg-white rounded-2xl shadow-sm border border-gray-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-gray-50 text-gray-500 font-semibold uppercase tracking-wider">
                <tr>
                  <th className="px-6 py-4">IFSC</th>
                  <th className="px-6 py-4">Branch Details</th>
                  <th className="px-6 py-4">Account Types</th>
                  <th className="px-6 py-4">Manager</th>
                  <th className="px-6 py-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {loading ? (
                  <tr>
                    <td colSpan={5} className="px-6 py-12 text-center text-gray-500">Loading branches...</td>
                  </tr>
                ) : branches.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-6 py-12 text-center text-gray-500">No branches found.</td>
                  </tr>
                ) : (
                  branches.map(b => (
                    <tr key={b.ifsc} className="hover:bg-gray-50 transition-colors">
                      <td className="px-6 py-4 font-mono font-semibold text-gray-900">{b.ifsc}</td>
                      <td className="px-6 py-4">
                        <div className="font-bold text-gray-900">{b.branch_name}</div>
                        <div className="text-xs text-gray-500 mt-1">{b.branch_address}</div>
                      </td>
                      <td className="px-6 py-4">
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-100">
                          {b.supported_account_type}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        {b.manager_name ? <div className="font-medium text-gray-900">{b.manager_name}</div> : <span className="text-gray-300">-</span>}
                        {b.manager_email && <div className="text-xs text-gray-500 mt-1">{b.manager_email}</div>}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <div className="flex justify-end gap-2">
                          <button 
                            onClick={() => openModal('edit', b.ifsc)}
                            className="p-2 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors"
                            title="Edit"
                          >
                            <Edit2 className="w-4 h-4" />
                          </button>
                          <button 
                            onClick={() => deleteBranch(b.ifsc)}
                            className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                            title="Delete"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Modal */}
      {modalOpen && (
        <div className="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-2xl w-full max-w-lg overflow-hidden shadow-2xl animate-in fade-in zoom-in-95 duration-200">
            <div className="px-6 py-4 border-b border-gray-100 flex justify-between items-center">
              <h2 className="text-xl font-bold text-gray-900">{modalMode === 'create' ? 'Add Branch' : 'Edit Branch'}</h2>
              <button onClick={() => setModalOpen(false)} className="text-gray-400 hover:text-gray-700">✕</button>
            </div>
            
            <form onSubmit={handleFormSubmit} className="px-6 py-4 space-y-4 max-h-[70vh] overflow-y-auto">
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-1">IFSC Code *</label>
                <input 
                  type="text" 
                  required 
                  disabled={modalMode === 'edit'}
                  value={formData.ifsc || ''}
                  onChange={e => setFormData({...formData, ifsc: e.target.value})}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                />
              </div>
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-1">Branch Name *</label>
                <input 
                  type="text" 
                  required 
                  value={formData.branch_name || ''}
                  onChange={e => setFormData({...formData, branch_name: e.target.value})}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                />
              </div>
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-1">Branch Address *</label>
                <textarea 
                  required 
                  rows={2}
                  value={formData.branch_address || ''}
                  onChange={e => setFormData({...formData, branch_address: e.target.value})}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                />
              </div>
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-1">Supported Account Types *</label>
                <input 
                  type="text" 
                  required 
                  placeholder="e.g. Retail, SME, Digital"
                  value={formData.supported_account_type || ''}
                  onChange={e => setFormData({...formData, supported_account_type: e.target.value})}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">Manager Name</label>
                  <input 
                    type="text" 
                    value={formData.manager_name || ''}
                    onChange={e => setFormData({...formData, manager_name: e.target.value})}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">Relationship Officer</label>
                  <input 
                    type="text" 
                    value={formData.relationship_officer || ''}
                    onChange={e => setFormData({...formData, relationship_officer: e.target.value})}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">Manager Email</label>
                  <input 
                    type="email" 
                    value={formData.manager_email || ''}
                    onChange={e => setFormData({...formData, manager_email: e.target.value})}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">Manager Phone</label>
                  <input 
                    type="text" 
                    value={formData.manager_phone || ''}
                    onChange={e => setFormData({...formData, manager_phone: e.target.value})}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                  />
                </div>
              </div>
              
              <div className="pt-4 flex justify-end gap-3 border-t border-gray-100">
                <button 
                  type="button" 
                  onClick={() => setModalOpen(false)}
                  className="px-4 py-2 bg-white border border-gray-300 text-gray-700 rounded-lg font-semibold hover:bg-gray-50 transition-colors"
                >
                  Cancel
                </button>
                <button 
                  type="submit" 
                  className="px-4 py-2 bg-emerald-600 text-white rounded-lg font-semibold hover:bg-emerald-700 transition-colors shadow-sm"
                >
                  Save Branch
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

import { useState, useEffect } from 'react';
import { ChevronLeft, UserCircle } from 'lucide-react';
import { Link } from 'react-router-dom';

interface ActiveAccount {
  id: string;
  name: string;
  email: string;
  phone: string;
  status: string;
  account_number: string;
  account_type: string;
  created_at: string;
  branch_ifsc: string;
}

export function AdminPanel() {
  const [accounts, setAccounts] = useState<ActiveAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedAccount, setSelectedAccount] = useState<ActiveAccount | null>(null);

  const fetchAccounts = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL || ''}/api/v1/admin/accounts/active`);
      if (res.ok) {
        const data = await res.json();
        setAccounts(data.accounts || []);
      }
    } catch (err) {
      console.error("Failed to fetch active accounts", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAccounts();
  }, []);

  if (selectedAccount) {
    return (
      <div className="min-h-screen bg-gray-50 p-6">
        <div className="max-w-4xl mx-auto">
          <button 
            onClick={() => setSelectedAccount(null)}
            className="flex items-center text-sm text-indigo-600 font-semibold mb-6 hover:text-indigo-800 transition-colors"
          >
            <ChevronLeft className="w-4 h-4 mr-1" />
            Back to Active Accounts
          </button>

          <div className="bg-white rounded-2xl shadow-xl border border-gray-100 overflow-hidden">
            <div className="bg-indigo-600 px-8 py-6 flex items-center justify-between">
              <div className="flex items-center text-white">
                <UserCircle className="w-16 h-16 mr-4 opacity-90" />
                <div>
                  <h2 className="text-2xl font-bold">{selectedAccount.name}</h2>
                  <p className="text-indigo-200 text-sm">{selectedAccount.email}</p>
                </div>
              </div>
              <div className="text-right">
                <p className="text-indigo-200 text-xs font-semibold uppercase tracking-wider mb-1">Account No.</p>
                <p className="text-2xl font-mono text-white tracking-widest">{selectedAccount.account_number}</p>
              </div>
            </div>

            <div className="p-8 grid grid-cols-1 sm:grid-cols-2 gap-8">
              <div>
                <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-4">Contact Details</h3>
                <div className="space-y-4 text-sm text-gray-900">
                  <div className="flex justify-between border-b border-gray-100 pb-2">
                    <span className="text-gray-500">Phone</span>
                    <span className="font-medium">{selectedAccount.phone}</span>
                  </div>
                  <div className="flex justify-between border-b border-gray-100 pb-2">
                    <span className="text-gray-500">Application ID</span>
                    <span className="font-mono text-xs">{selectedAccount.id}</span>
                  </div>
                  <div className="flex justify-between border-b border-gray-100 pb-2">
                    <span className="text-gray-500">Created At</span>
                    <span>{selectedAccount.created_at ? new Date(selectedAccount.created_at).toLocaleString() : '—'}</span>
                  </div>
                </div>
              </div>

              <div>
                <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-4">Banking Details</h3>
                <div className="space-y-4 text-sm text-gray-900">
                  <div className="flex justify-between border-b border-gray-100 pb-2">
                    <span className="text-gray-500">Account Type</span>
                    <span className="font-medium capitalize">{selectedAccount.account_type?.replace('_', ' ')}</span>
                  </div>
                  <div className="flex justify-between border-b border-gray-100 pb-2">
                    <span className="text-gray-500">Status</span>
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-100 text-emerald-800">
                      {selectedAccount.status}
                    </span>
                  </div>
                  <div className="flex justify-between border-b border-gray-100 pb-2">
                    <span className="text-gray-500">Preferred Branch (IFSC)</span>
                    <span className="font-mono text-xs">{selectedAccount.branch_ifsc || 'N/A'}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col pt-8 px-4 sm:px-6 lg:px-8">
       <div className="max-w-5xl mx-auto w-full">
         <div className="flex items-center mb-2">
            <Link to="/" className="text-sm text-gray-500 hover:text-indigo-600 flex items-center transition-colors">
              <ChevronLeft className="w-4 h-4 mr-1" /> Dashboard
            </Link>
         </div>
         <div className="flex justify-between items-center mb-8">
           <h1 className="text-3xl font-extrabold tracking-tight text-gray-900">Active Accounts</h1>
           <button 
             onClick={fetchAccounts}
             className="text-sm px-4 py-2 bg-white border border-gray-200 text-gray-700 rounded-lg font-semibold hover:bg-gray-50 hover:text-indigo-600 transition-colors shadow-sm"
           >
             Refresh List
           </button>
         </div>

         {loading ? (
           <div className="flex justify-center py-20">
             <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600"></div>
           </div>
         ) : accounts.length === 0 ? (
           <div className="bg-white rounded-2xl shadow-sm border border-gray-200 p-16 text-center text-gray-500">
             <UserCircle className="w-16 h-16 mx-auto text-gray-300 mb-4" />
             <p className="text-lg font-medium text-gray-900 mb-1">No Active Accounts</p>
             <p className="text-sm">There are currently no accounts with assigned account numbers.</p>
           </div>
         ) : (
           <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
             {accounts.map(acc => (
               <div 
                 key={acc.id} 
                 className="bg-white rounded-2xl shadow-sm border border-gray-200 p-6 hover:shadow-lg hover:border-indigo-300 cursor-pointer transition-all duration-200 group"
                 onClick={() => setSelectedAccount(acc)}
               >
                 <div className="flex justify-between items-start mb-4">
                   <div className="flex items-center">
                     <div className="w-10 h-10 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-600 font-bold mr-3">
                       {acc.name?.charAt(0).toUpperCase() || 'U'}
                     </div>
                     <div>
                       <p className="text-sm font-bold text-gray-900 group-hover:text-indigo-600 transition-colors">{acc.name || 'Unknown'}</p>
                       <p className="text-xs text-gray-500">{acc.email}</p>
                     </div>
                   </div>
                   <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-100 text-emerald-800">
                     Active
                   </span>
                 </div>
                 <div className="bg-gray-50 rounded-lg p-3 border border-gray-100 mb-3">
                    <p className="text-[10px] text-gray-400 font-bold uppercase tracking-wider mb-1">Account Number</p>
                    <p className="text-lg font-mono text-gray-900 tracking-widest">{acc.account_number}</p>
                 </div>
                 <div className="flex justify-between items-center text-xs text-gray-500">
                    <span className="capitalize">{acc.account_type?.replace('_', ' ')}</span>
                    <span className="font-medium text-indigo-600 group-hover:underline">View Details &rarr;</span>
                 </div>
               </div>
             ))}
           </div>
         )}
       </div>
    </div>
  );
}

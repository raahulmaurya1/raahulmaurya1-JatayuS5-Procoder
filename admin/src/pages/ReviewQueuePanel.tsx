import { useState, useEffect } from 'react';
import { RiskReviewPage } from './RiskReviewPage';
import { ChevronLeft } from 'lucide-react';
import { Link } from 'react-router-dom';

interface PendingUser {
  id: string;
  name: string;
  email: string;
  phone: string;
  status: string;
  created_at: string;
}

export function ReviewQueuePanel() {
  const [queue, setQueue] = useState<PendingUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);

  const fetchQueue = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL || ''}/api/v1/risk-review/pending`);
      if (res.ok) {
        const data = await res.json();
        setQueue(data.queue || []);
      }
    } catch (err) {
      console.error("Failed to fetch queue", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchQueue();
  }, []);

  if (selectedUserId) {
    return (
      <div className="min-h-screen bg-gray-50 flex flex-col relative">
        <div className="absolute top-4 left-4 z-50">
          <button 
            onClick={() => {
              setSelectedUserId(null);
              fetchQueue(); 
            }} 
            className="flex items-center space-x-2 px-4 py-2 bg-white text-gray-700 font-medium rounded-md shadow hover:bg-gray-50 border border-gray-200"
          >
             <ChevronLeft className="w-5 h-5 text-gray-500" />
             <span>Back to Queue</span>
          </button>
        </div>
        <div className="flex-1 pt-16">
          <RiskReviewPage userId={selectedUserId} />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col pt-8 px-4 sm:px-6 lg:px-8">
       <div className="max-w-4xl mx-auto w-full">
         <div className="flex items-center mb-2">
            <Link to="/" className="text-sm text-gray-500 hover:text-indigo-600 flex items-center transition-colors">
              <ChevronLeft className="w-4 h-4 mr-1" /> Dashboard
            </Link>
         </div>
         <div className="flex justify-between items-center mb-8">
           <h1 className="text-3xl font-extrabold tracking-tight text-gray-900">Review Queue</h1>
           <button 
             onClick={fetchQueue}
             className="text-sm px-4 py-2 bg-indigo-50 text-indigo-700 rounded-md font-semibold hover:bg-indigo-100 transition-colors"
           >
             Refresh
           </button>
         </div>

         {loading ? (
           <div className="text-center py-12 text-gray-500 font-medium">Loading unreviewed applications...</div>
         ) : queue.length === 0 ? (
           <div className="bg-white rounded-2xl shadow border border-gray-200 p-12 text-center text-gray-500">
             Inbox Zero! No pending applications require review.
           </div>
         ) : (
           <div className="bg-white rounded-2xl shadow border border-gray-200 overflow-hidden">
             <ul className="divide-y divide-gray-200">
               {queue.map(user => (
                 <li 
                   key={user.id} 
                   className="p-4 hover:bg-indigo-50 cursor-pointer transition-colors duration-200 flex justify-between items-center group"
                   onClick={() => setSelectedUserId(user.id)}
                 >
                   <div>
                     <p className="text-sm font-semibold text-indigo-600 group-hover:text-indigo-800 transition-colors">{user.name || 'Unknown Applicant'}</p>
                     <p className="text-xs text-gray-500">{user.email}</p>
                     <p className="text-xs text-gray-400 mt-0.5">{user.phone}</p>
                     <p className="text-xs text-gray-400 font-mono mt-0.5">ID: {user.id}</p>
                   </div>
                   <div className="text-right flex flex-col items-end space-y-1">
                     <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-amber-100 text-amber-800">
                       {user.status}
                     </span>
                     <p className="text-xs text-gray-500">
                       {user.created_at ? new Date(user.created_at).toLocaleString() : '—'}
                     </p>
                   </div>
                 </li>
               ))}
             </ul>
           </div>
         )}
       </div>
    </div>
  );
}

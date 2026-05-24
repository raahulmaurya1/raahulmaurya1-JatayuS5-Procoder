import { Link } from 'react-router-dom';
import { Users, FileSearch, Building2, ChevronRight } from 'lucide-react';

export function Dashboard() {
  const cards = [
    {
      title: 'Admin Panel',
      description: 'View and manage all active accounts',
      icon: Users,
      color: 'bg-blue-500',
      hover: 'hover:border-blue-400 hover:shadow-blue-500/20',
      link: '/admin-panel'
    },
    {
      title: 'Review Queue Panel',
      description: 'Approve or reject pending applications',
      icon: FileSearch,
      color: 'bg-amber-500',
      hover: 'hover:border-amber-400 hover:shadow-amber-500/20',
      link: '/review-queue'
    },
    {
      title: 'Bank Branch Panel',
      description: 'Manage branches, add or edit details',
      icon: Building2,
      color: 'bg-emerald-500',
      hover: 'hover:border-emerald-400 hover:shadow-emerald-500/20',
      link: '/bank-branches'
    }
  ];

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col pt-12 px-4 sm:px-6 lg:px-8">
      <div className="max-w-6xl mx-auto w-full">
        <div className="mb-12">
          <h1 className="text-4xl font-extrabold tracking-tight text-gray-900 mb-2">Dashboard</h1>
          <p className="text-lg text-gray-500">Manage accounts, reviews, and branches from one central hub.</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-12">
          {cards.map((card) => {
            const Icon = card.icon;
            return (
              <Link
                key={card.title}
                to={card.link}
                className={`block bg-white rounded-2xl p-6 border-2 border-transparent shadow-md transition-all duration-300 transform hover:-translate-y-1 ${card.hover} group`}
              >
                <div className={`w-12 h-12 rounded-xl flex items-center justify-center mb-6 text-white ${card.color} shadow-lg`}>
                  <Icon className="w-6 h-6" />
                </div>
                <h3 className="text-xl font-bold text-gray-900 mb-2 group-hover:text-indigo-600 transition-colors">{card.title}</h3>
                <p className="text-gray-500 mb-6">{card.description}</p>
                <div className="flex items-center text-sm font-semibold text-indigo-600">
                  Open Panel
                  <ChevronRight className="w-4 h-4 ml-1 group-hover:translate-x-1 transition-transform" />
                </div>
              </Link>
            );
          })}
        </div>

        <div className="bg-white rounded-2xl p-6 border-2 border-dashed border-gray-300 shadow-sm relative overflow-hidden">
          <div className="absolute top-0 left-0 w-1 h-full bg-indigo-500"></div>
          <h3 className="text-lg font-bold text-gray-900 mb-2">Instructions</h3>
          <p className="text-gray-600 text-sm leading-relaxed">
            Welcome to the Admin Dashboard.
            <br />
            System Logs & Issue Tracking: Monitor real-time errors and system health via <a href="https://c-v-raman-global-university.sentry.io/issues/?project=4511372458459136">Sentry</a> and <a href="https://join.slack.com/t/procoder-talk/shared_invite/zt-3xoi8312e-lbyx3ep8U2LGwKWEbwdfpQ">Slack</a>.
            <br />
            Database Management: Access and manage data infrastructure directly through <a href="http://supabase.com/dashboard/project/ugjgxejjdiccmjljwirn/database">Supabase</a>.
          </p>
        </div>
      </div>
    </div>
  );
}

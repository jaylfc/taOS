// stub for the red run
#include "spaces.h"
namespace Spaces {
QString defaultLabel(int p){return QString::number(p);} QString cleanName(const QString&r){return r;}
QString Store::defaultPath(){return {};} bool Store::load(QString*){return true;} bool Store::save(QString*)const{return true;}
bool Store::has(const QString&)const{return false;} QString Store::outputOf(const QString&)const{return {};}
int Store::positionOf(const QString&,const QString&)const{return -1;} QString Store::label(const QString&,const QString&k)const{return k;}
QString Store::add(const QString&,const QString&,const QString&){return {};} QString Store::rename(const QString&,const QString&){return {};}
QString Store::move(const QString&,int){return {};} QString Store::remove(const QString&){return {};} void Store::relocate(const QString&,const QString&){}
QString Store::freshKey(const QStringList&)const{return "1";}
QString Engine::wsRef(const QString&k){return k;} QString Engine::save(){return {};}
void Engine::sync(const Snapshot&s,const QString&o){m_snap=s;m_output=o;}
QVector<View> Engine::views()const{QVector<View> v; for(const WsInfo&w:m_snap.workspaces){View x;x.key=w.name;x.label=w.name;x.position=v.size()+1;v.push_back(x);} return v;}
QString Engine::add(const QString&,QString*){return {};} QString Engine::rename(const QString&,const QString&){return {};}
QString Engine::move(const QString&,int){return {};} QString Engine::remove(const QString&){return {};} QString Engine::go(const QString&){return {};}
QString Engine::moveWindow(int,const QString&){return {};} QString Engine::keyFor(const QString&)const{return {};} QString Engine::handle(const QStringList&){return "ok";}
}

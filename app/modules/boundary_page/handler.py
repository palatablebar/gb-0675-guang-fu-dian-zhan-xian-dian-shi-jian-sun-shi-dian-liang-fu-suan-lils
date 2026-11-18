from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse
from app.web import templates
from app import store, governance

router=APIRouter()

@router.get('/boundary')
def page(request:Request):
    from app.main import DB_PATH
    overview=governance.overview(DB_PATH)
    return templates.TemplateResponse(request=request,name='boundary.html',
                                      context={'meta':None,'key':'boundary','state':None,'view':None,
                                               'overview':overview,'boundary':overview['boundary']})

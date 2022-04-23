from django.shortcuts import render
from translator_data.translator import evaluate


def index(request):
    if request.method == "POST":

        word = request.POST.get("word")
        language = request.POST.get("language")

        try:
            results = evaluate(word, language, 60)
            result = ""
            for re in results:
                result += ' ' + re
        except KeyError:
            result = None
        if result is not None:
            return render(request, 'index.html', {'word': word, 'translation': str(result)})
        else:
            result = "Sorry! No translation found for your word(s)! Make sure you type correct word(s)"
            return render(request, 'index.html', {'word': word, 'translation': result})
    else:
        return render(request, 'index.html')


def about(request):
    return render(request, 'about.html')

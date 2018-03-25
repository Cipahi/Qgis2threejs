# -*- coding: utf-8 -*-
"""
/***************************************************************************
 Q3DView

                              -------------------
        begin                : 2016-02-10
        copyright            : (C) 2016 Minoru Akagi
        email                : akaginch@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
from datetime import datetime
import os

#from PyQt5.Qt import *
from PyQt5.QtCore import Qt, QByteArray, QBuffer, QDir, QIODevice, QObject, QUrl, pyqtSignal, pyqtSlot, qDebug
from PyQt5.QtGui import QImage, QPainter, QPalette
from PyQt5.QtWebKitWidgets import QWebPage, QWebView
from PyQt5.QtWidgets import QFileDialog

from .conf import debug_mode
from .qgis2threejstools import pluginDir

def base64image(image):
  ba = QByteArray()
  buffer = QBuffer(ba)
  buffer.open(QIODevice.WriteOnly)
  image.save(buffer, "PNG")
  return "data:image/png;base64," + ba.toBase64().data().decode("ascii")


class Bridge(QObject):

  # Python to JS
  sendData = pyqtSignal("QVariant")

  # Python to Python
  imageReceived = pyqtSignal(int, int, "QImage")

  def __init__(self, parent=None):
    QObject.__init__(self, parent)
    self._parent = parent

  # examples
  @pyqtSlot(int, int, result=str)
  def mouseUpMessage(self, x, y):
    return "Clicked at ({0}, {1})".format(x, y)
    # JS side: console.log(pyObj.mouseUpMessage(e.clientX, e.clientY));

  @pyqtSlot(int, int, str)
  def saveImage(self, width, height, dataUrl):
    image = None
    if dataUrl:
      ba = QByteArray.fromBase64(dataUrl[22:].encode("ascii"))
      image = QImage()
      image.loadFromData(ba)
    self.imageReceived.emit(width, height, image)


class Q3DWebPage(QWebPage):

  consoleMessage = pyqtSignal(str, int, str)

  def __init__(self, parent=None):
    QWebPage.__init__(self, parent)

    if debug_mode == 2:
      # open log file
      self.logfile = open(pluginDir("q3dview.log"), "w")

  def javaScriptConsoleMessage(self, message, lineNumber, sourceID):
    self.consoleMessage.emit(message, lineNumber, sourceID)

    if debug_mode == 2:
      now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
      self.logfile.write("{} {} ({}: {})\n".format(now, message, sourceID, lineNumber))
      self.logfile.flush()


class Q3DView(QWebView):

  def __init__(self, parent=None):
    QWebView.__init__(self, parent)

    self.requestQueue = []
    self.isProcessingExclusively = False

  def setup(self, wnd, iface, isViewer=True, enabled=True):
    self.wnd = wnd
    self.iface = iface
    self.isViewer = isViewer
    self._enabled = enabled
    self.bridge = Bridge(self)
    self.bridge.imageReceived.connect(self.saveImage)

    self.loadFinished.connect(self.pageLoaded)

    self._page = Q3DWebPage(self)
    self._page.consoleMessage.connect(wnd.printConsoleMessage)
    self._page.mainFrame().javaScriptWindowObjectCleared.connect(self.addJSObject)
    self.setPage(self._page)

    if not isViewer:
      # transparent background
      palette = self._page.palette()
      palette.setBrush(QPalette.Base, Qt.transparent)
      self._page.setPalette(palette)
      self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    self.renderId = None    # for renderer

    filetitle = "viewer" if isViewer else "layer"
    url = os.path.join(os.path.abspath(os.path.dirname(__file__)), "viewer", filetitle + ".html").replace("\\", "/")
    self.setUrl(QUrl.fromLocalFile(url))

  def reloadPage(self):
    self.wnd.clearConsole()
    self.setUrl(self.url())
    #self.reload()

  def addJSObject(self):
    self._page.mainFrame().addToJavaScriptWindowObject("pyObj", self.bridge)
    if debug_mode:
      self.wnd.printConsoleMessage("pyObj added", sourceID="q3dview.py")

  def pageLoaded(self, ok):
    self.runString("pyObj.sendData.connect(this, dataReceived);")

    # start application - enable controls
    self.iface.startApplication()

    if self._enabled:
      # create scene and layers
      self.iface.exportScene()

      for layer in self.iface.controller.settings.getLayerList():
        if layer.visible:
          self.iface.exportLayer(layer)
    else:
      self.iface.setPreviewEnabled(False)

  def showStatusMessage(self, msg):
    self.wnd.ui.statusbar.showMessage(msg)

  def reload(self):
    pass

  def resetCameraPosition(self):
    self.runString("app.controls.reset();")

  def runString(self, string):
    if debug_mode:
      self.wnd.printConsoleMessage(string, sourceID="runString")
      qDebug("runString: {}\n".format(string))

      if debug_mode == 2:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._page.logfile.write("{} runString: {}\n".format(now, string))
        self._page.logfile.flush()

    return self._page.mainFrame().evaluateJavaScript(string)

  def saveImage(self, width, height, image):
    if image is None:
      image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
      painter = QPainter(image)
      self._page.mainFrame().render(painter)
      painter.end()

    filename, _ = QFileDialog.getSaveFileName(self, self.tr("Save As"), QDir.homePath(), "PNG file (*.png)")
    if filename:
      image.save(filename)
